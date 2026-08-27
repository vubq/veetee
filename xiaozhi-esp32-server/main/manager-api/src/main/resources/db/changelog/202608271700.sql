UPDATE ai_model_provider
SET fields = JSON_ARRAY_APPEND(
    fields,
    '$', JSON_OBJECT(
        'key', 'request_overrides',
        'type', 'dict',
        'label', '请求参数覆盖(JSON)'
    ),
    '$', JSON_OBJECT(
        'key', 'realtime_router',
        'type', 'dict',
        'label', '实时工具路由(JSON)'
    )
)
WHERE id = 'SYSTEM_LLM_openai'
  AND JSON_SEARCH(fields, 'one', 'request_overrides', NULL, '$[*].key') IS NULL;
