-- SDXL Turbo accepts negative prompts in the UI and service; align the remote
-- catalog row with the compiled fallback (models.py).
UPDATE catalog_entries
SET payload = jsonb_set(payload, '{supports_negative_prompt}', 'true'::jsonb, true)
WHERE kind = 'image_gen_model'
  AND key = 'stabilityai/sdxl-turbo';
