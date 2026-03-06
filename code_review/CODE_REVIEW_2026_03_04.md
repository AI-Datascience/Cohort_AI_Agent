# コードレビュー: マッチングロジックの品質問題

**レビュー日**: 2026-03-04
**対象**: `main_cohort.ipynb` / `create_caption_vector.ipynb` のコアマッチングロジック
**正解参照**: `function_bp_form_cohortmatch.py`（`weighted_businesslist` による直接マッピング方式）

---

## 処理フローの概観

```
[create_caption_vector.ipynb]
  cohort.npz の business_codelist (ジャンルID順) を取得
  → Sparkテーブルで ジャンルID → 場所名 に変換 (dict_code2name)
  → 場所名をキーにして LLM でシーン文を生成
  → SentenceTransformer でベクトル化
  → data_list（ジャンルID順の場所名）に合わせてソート
  → cohort_caption_matrix.npz として保存 (spots_matrix: 2541×512)

[main_cohort.ipynb]
  cohort.npz を読み込み (np_cohort: 数千万×2541 疎行列)
  cohort_caption_matrix.npz を読み込み (spots_matrix: 2541×512)
  → assert で名前配列の一致を検証
  → LP分析キーワードを spots_matrix 経由で 2541次元に射影 (lp_coefficient)
  → np_cohort @ lp_coefficient.T でスコア算出
```

---

## 問題1: spots_matrix の次元整合性 — 場所名キーの落とし穴 🔴

### 核心

`cohort.npz` の2541次元は**ジャンルID（BUSINESS_CODE）** で定義されている。
しかし `create_caption_vector.ipynb` は途中から**場所名（BUSINESS_NAME_S）をキー**にして処理している。

**ジャンルIDと場所名が1:1でない場合、次元がずれる。**

### データフローの追跡

```
cohort.npz
  np_codelist = ["CODE_001", "CODE_002", "CODE_003", "CODE_004", ...]  (2541個、ジャンルID順)

Spark テーブル (navit_business)
  CODE_001 → "フィットネスクラブ"
  CODE_002 → "レストラン"
  CODE_003 → "レストラン"     ← 異なるコードが同一名称
  CODE_004 → "オートキャンプ場"

data_list = ["フィットネスクラブ", "レストラン", "レストラン", "オートキャンプ場"]  (2541個)
```

#### Step 1: place_names 辞書で後勝ち

```python
place_names = {elem:idx for idx, elem in enumerate(data_list)}
# → {"フィットネスクラブ": 0, "レストラン": 2, "オートキャンプ場": 3}
#    ↑ "レストラン" のインデックス 1 が 2 で上書き
```

#### Step 2: LLM 生成結果も場所名キー

```python
final_data = {"フィットネスクラブ": [...], "レストラン": [...], "オートキャンプ場": [...]}
# → 3エントリ（"レストラン" は1つにまとまる）
```

#### Step 3: ソート後の行列

```python
final_matrix       # (3, 512) — 3ジャンル分しかない
sorted_matrix      # (3, 512) — ソートしても3行のまま
sorted_places      # (4,)     — data_list は4要素のまま
```

#### 結果: NPZ内で行数不一致

```
cohort_caption_matrix.npz:
  data             = (3, 512)   ← 場所名の重複で1行少ない
  business_placelist = (4,)     ← data_list そのまま
```

#### main_cohort.ipynb で発生するエラー

```python
spots_matrix.T                    # (512, 3)
lp_coefficient = ... @ spots_matrix.T  # (1, 3)
np_cohort @ lp_coefficient.T      # (N, 2541) × (3, 1) → 次元不一致エラー
```

### assert が検出できない理由

```python
assert np.array_equal(
    np.array([dict_code2name[code] for code in np_codelist]),  # (2541,)
    relational_spots                                            # (2541,) ← sorted_places
)
```

この assert は `relational_spots`（場所名配列）と `np_codelist` から変換した名前の一致のみ検証。
**`spots_matrix` の行数は一切チェックしていない。**

### 正解コードとの比較

正解コードは**ジャンルID（BUSINESS_CODE）を一貫してキー**にしている。場所名はあくまで表示用で、処理の軸はコード:

```python
# 正解コード: ジャンルIDベースの処理
name = item.get("name", "")          # 場所名を取得
resolved = _normalize_value(name, biz_list, biz_set)  # → 正規化された場所名
normalized.append({"name": nv, "weight": weight})      # 場所名+重み
```

正解コードの `weighted_businesslist` は場所名を使うが、`_normalize_value` で正規リストに照合し、最終的にコホートの次元と1:1対応を保証している。

### 修正の方向性

`create_caption_vector.ipynb` で**場所名ではなくジャンルIDをキー**にして全処理を行う。
LLMへの入力は場所名で構わないが、内部管理は常にジャンルIDで行い、1:1対応を保証する。

---

## 問題2: true_danger（LLM生成失敗）で次元が欠損する 🔴

### 何が起きるか

LLMが5回リトライしても特定の場所のシーン文生成に失敗した場合、`true_danger` に追加され、`final_data` から欠落する。

```python
final_data = analysis_data | correct_data  # true_danger のキーは含まれない
```

2541ジャンル中1つでも欠損すると、`spots_matrix` は2540行になり、`np_cohort`（2541列）との行列積が次元不一致で失敗する。

### 現状の対処

```python
# ノートブックのコメント:
# true_dangerがどうしても、発生してしまう
# このセルで手動で、問題の解決を図ること
```

手動対処に依存しており、欠損のまま後続処理に進む防止策がない。

### 修正の方向性

- `final_data` のキー数と `data_list` の長さの一致を保存前に検証する
- 欠損があった場合、ゼロベクトルなどのフォールバックを入れる

---

## 問題3: Positive/Negative のベクトル空間での相殺 🔴

### 何が起きているか

Negative キーワードに負の重みを付けてベクトル空間で引き算し、1本のクエリベクトルに合成している。

```
positive: 「雨上がりのオートキャンプ場」 +0.9
negative: 「炎天下のテニスコート」       -0.9
→ 「アウトドア」の意味成分が相殺される
→ キャンプ系ADIDのスコアが不当に低下
```

LLMが出す Positive/Negative は同一商品ドメインのキーワードであるため、意味成分の重複は避けられず、この相殺は高確率で発生する。

さらに、重みを `Σ|w|` で正規化しているため、Negative の件数が増えるほど Positive 1件あたりの実効重みが小さくなる。

### 正解コードとの比較

正解コードの `weighted_businesslist` は正の重みのみで構成されている。

### 修正の方向性

- Positive のみでスコアを算出する
- Negative を使うなら後段でペナルティとして適用する（2パス方式）

---

## 問題4: L2正規化のコメントアウト — ランキングが「活動量」順になっている 🔴

### 何が起きているか

数千万ADID × 2541次元の疎行列 `np_cohort` に対する行ごとのL2正規化がコメントアウトされている。

正規化なしの `np_cohort @ lp_coefficient.T` では、非ゼロ要素が多い行（= 多くの場所に出現するADID）のスコアが自然に大きくなる。

### 結果

- ヘビーユーザーが常に上位に来る
- ニッチだが高適合なADID（例: キャンプ場にしか行かない人）が埋もれる
- **5GBの疎行列が持つ次元ごとの特徴パターンが活かされない**

---

## 問題5: MAX_RECORDS = 10,000 — 母集団に対して極端に少ない 🟠

数千万ADIDから上位10,000件のみを抽出しており、カバー率は0.02〜0.1%。広告配信のターゲティング用途であれば桁が足りない可能性が高い。

---

## 問題6: 情報ボトルネック — ベクトル空間経由の間接マッピング 🟠

```
10〜20キーワード → 重み付き平均 → 1本の512次元ベクトル → 2541次元に射影
```

重み付き平均で1本のベクトルに集約される時点で個々のキーワードの固有性が薄まり、2541次元のスポット係数の差がつきにくくなる。

正解コードはコホートキャプション名に直接重み付けし、名寄せで確実にマッピングするため、この情報損失が発生しない。

---

## 問題の複合的影響

```
問題1: spots_matrix とNPZの次元がずれるリスク（場所名キーの罠）
  ↓
問題3: Negative相殺で lp_coefficient の信号が弱まる
  ↓
問題6: 重み付き平均でさらに差が小さくなる
  ↓
問題4: L2正規化なしで弱い信号が行動量バイアスに埋もれる
  ↓
問題5: バイアスのかかったランキングから0.02%だけ取る
```

5GBの疎行列データが持つマッチング精度のポテンシャルが、各段階の問題により連鎖的に劣化している。

---

## コード上の注意点（最低限）

| 項目 | 対応 |
|------|------|
| APIキーのコンソール出力 | `main_cohort.ipynb`, `create_caption_vector.ipynb` の該当 print文を削除 |
| CSR行列に `.row`/`.col` でアクセス | `.tocoo()` 変換が必要 |
| `llm_agent.py` の `complete()` | `max_tokens`, `temperature`, `top_p` がAPI呼び出しに渡されていない |
