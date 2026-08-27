UPDATE ai_model_provider
SET fields = JSON_ARRAY_APPEND(
    fields,
    '$', JSON_OBJECT(
        'key', 'parallel_workers',
        'type', 'number',
        'label', '并行合成任务数'
    ),
    '$', JSON_OBJECT(
        'key', 'first_segment_chars',
        'type', 'number',
        'label', '首段触发字符数'
    ),
    '$', JSON_OBJECT(
        'key', 'segment_chars',
        'type', 'number',
        'label', '后续分段字符数'
    )
)
WHERE id = 'SYSTEM_TTS_edge'
  AND JSON_SEARCH(fields, 'one', 'parallel_workers', NULL, '$[*].key') IS NULL;
