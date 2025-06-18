-- Use a WITH clause for the image embeddings
WITH image_embeddings AS (
  SELECT *
  FROM
    ML.GENERATE_EMBEDDING(
      MODEL `patient_records.multimodal_embedding_model`,
      (SELECT * FROM `patient_records.encounters` WHERE content_type = 'image/jpeg')
    )
),
referral_embeddings AS (
    SELECT
        patient_name,
        dob,
        referring_facility,
        referring_provider,
        provisional_diagnosis,
        referral_date,
        referral_expiration_date,
        category_of_care,
        service_requested,
        content_embedding
    FROM
        `patient_records.referrals`
        WHERE 
        -- Example procedure date '2025-03-01' - this would be replaced with a parameter from frontend
        DATE('2025-03-01') BETWEEN referral_date AND referral_expiration_date
)
-- Create the vector search results table
SELECT 
  base.*, distance FROM VECTOR_SEARCH ( TABLE patient_records.referrals,
      'content_embedding',
      TABLE image_embeddings,
      top_k => 3 )
