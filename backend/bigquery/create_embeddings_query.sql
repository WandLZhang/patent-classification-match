-- First, add the embedding column to the table
ALTER TABLE `gemini-med-lit-review.patents.patent_records`
ADD COLUMN IF NOT EXISTS content_embedding ARRAY<FLOAT64>;

-- Then, update the table with embeddings
UPDATE `gemini-med-lit-review.patents.patent_records` r
SET r.content_embedding = e.ml_generate_embedding_result
FROM (
  SELECT 
    content,
    ml_generate_embedding_result
  FROM
    ML.GENERATE_EMBEDDING(
      MODEL `patents.gemini_embedding_model`,
      (SELECT 
        CONCAT(
          patent_title, " ",
          ARRAY_TO_STRING(cpc_code, " "), " ",
          ARRAY_TO_STRING(ipc_code, " "), " ",
          abstract, " ",
          description, " ",
          claims
        ) as content 
      FROM `gemini-med-lit-review.patents.patent_records`)
    )
) e
WHERE CONCAT(
  r.patent_title, " ",
  ARRAY_TO_STRING(r.cpc_code, " "), " ",
  ARRAY_TO_STRING(r.ipc_code, " "), " ",
  r.abstract, " ",
  r.description, " ",
  r.claims
) = e.content;
