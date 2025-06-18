-- First, ensure the embedding column exists
ALTER TABLE `gemini-med-lit-review.patents.patent_records`
ADD COLUMN IF NOT EXISTS content_embedding ARRAY<FLOAT64>;

-- Simple approach: Process one record at a time with basic content
-- This approach is slower but more reliable for troubleshooting

-- Option 1: Update with just title and abstract (shorter content)
UPDATE `gemini-med-lit-review.patents.patent_records`
SET content_embedding = (
  SELECT ml_generate_embedding_result
  FROM ML.GENERATE_EMBEDDING(
    MODEL `patient_records.multimodal_embedding_model`,
    (SELECT CONCAT(
      COALESCE(patent_title, ''), 
      '. Abstract: ',
      COALESCE(abstract, '')
    ) as content)
  )
)
WHERE patent_title IS NOT NULL 
  AND content_embedding IS NULL;

-- Option 2: If the above works, then try with more content but truncated
/*
UPDATE `gemini-med-lit-review.patents.patent_records`
SET content_embedding = (
  SELECT ml_generate_embedding_result
  FROM ML.GENERATE_EMBEDDING(
    MODEL `patient_records.multimodal_embedding_model`,
    (SELECT SUBSTR(
      CONCAT(
        COALESCE(patent_title, ''), ' ',
        'CPC: ', COALESCE(ARRAY_TO_STRING(cpc_code, ', '), ''), ' ',
        'IPC: ', COALESCE(ARRAY_TO_STRING(ipc_code, ', '), ''), ' ',
        'Abstract: ', COALESCE(abstract, ''), ' ',
        'Description: ', COALESCE(SUBSTR(description, 1, 500), ''), ' ',
        'Claims: ', COALESCE(SUBSTR(claims, 1, 500), '')
      ), 1, 5000) as content)  -- Limit total content to 5000 chars
  )
)
WHERE patent_title IS NOT NULL 
  AND content_embedding IS NULL;
*/

-- Option 3: Batch processing with explicit content format
/*
CREATE OR REPLACE TABLE `gemini-med-lit-review.patents.patent_records_with_embeddings` AS
WITH patent_content AS (
  SELECT 
    *,
    CONCAT(
      COALESCE(patent_title, ''), 
      '. Abstract: ',
      COALESCE(abstract, '')
    ) as embedding_content
  FROM `gemini-med-lit-review.patents.patent_records`
),
embeddings AS (
  SELECT 
    embedding_content,
    ml_generate_embedding_result
  FROM ML.GENERATE_EMBEDDING(
    MODEL `patient_records.multimodal_embedding_model`,
    (SELECT DISTINCT embedding_content as content FROM patent_content)
  )
)
SELECT 
  p.*,
  e.ml_generate_embedding_result as content_embedding
FROM patent_content p
LEFT JOIN embeddings e ON p.embedding_content = e.embedding_content;
*/

-- Option 4: If multimodal model expects specific format, try this
/*
UPDATE `gemini-med-lit-review.patents.patent_records`
SET content_embedding = (
  SELECT ml_generate_embedding_result[OFFSET(0)]  -- Get first element if array
  FROM ML.GENERATE_EMBEDDING(
    MODEL `patient_records.multimodal_embedding_model`,
    (SELECT TO_JSON_STRING(STRUCT(
      patent_title as title,
      abstract as summary,
      '' as image_uri  -- multimodal models might expect this field
    )) as content)
  )
)
WHERE patent_title IS NOT NULL 
  AND content_embedding IS NULL
LIMIT 1;  -- Test with just one record first
*/
