-- First, add the embedding column to the table
ALTER TABLE `patient_records.referrals`
ADD COLUMN IF NOT EXISTS content_embedding ARRAY<FLOAT64>;

-- Then, update the table with embeddings
UPDATE `patient_records.referrals` r
SET r.content_embedding = e.ml_generate_embedding_result
FROM (
  SELECT 
    content,
    ml_generate_embedding_result
  FROM
    ML.GENERATE_EMBEDDING(
      MODEL `patient_records.multimodal_embedding_model`,
      (SELECT 
        CONCAT(
          patient_name, " ",
          dob, " ",
          CAST(age AS STRING), " ",
          referring_facility, " ",
          referring_facility_phone, " ",
          referring_facility_fax, " ",
          referring_provider, " ",
          npi, " ",
          priority, " ",
          provisional_diagnosis, " ",
          referral_date, " ",
          clinically_indicated_date, " ",
          referral_expiration_date, " ",
          referral_category, " ",
          level_of_care_coordination, " ",
          category_of_care, " ",
          service_requested
        ) as content 
      FROM `patient_records.referrals`)
    )
) e
WHERE CONCAT(
  r.patient_name, " ",
  r.dob, " ",
  CAST(r.age AS STRING), " ",
  r.referring_facility, " ",
  r.referring_facility_phone, " ",
  r.referring_facility_fax, " ",
  r.referring_provider, " ",
  r.npi, " ",
  r.priority, " ",
  r.provisional_diagnosis, " ",
  r.referral_date, " ",
  r.clinically_indicated_date, " ",
  r.referral_expiration_date, " ",
  r.referral_category, " ",
  r.level_of_care_coordination, " ",
  r.category_of_care, " ",
  r.service_requested
) = e.content;
