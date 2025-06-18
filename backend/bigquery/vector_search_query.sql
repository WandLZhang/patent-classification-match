-- Use a WITH clause for the query string embedding
WITH query_embedding AS (
  SELECT ml_generate_embedding_result
  FROM
    ML.GENERATE_EMBEDDING(
      MODEL `gemini-med-lit-review.patents.gemini_embedding_model`,
      (SELECT @query_string AS content)  -- Parameter from frontend
    )
)
-- Perform the vector search
SELECT 
  base.*,
  distance
FROM 
  VECTOR_SEARCH(
    TABLE `gemini-med-lit-review.patents.patent_records`,
    'content_embedding',
    (SELECT ml_generate_embedding_result FROM query_embedding),
    top_k => 5,
    distance_type => 'COSINE'
  )
