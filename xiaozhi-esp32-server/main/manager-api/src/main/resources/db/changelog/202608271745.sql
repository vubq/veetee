INSERT INTO sys_params (id, param_code, param_value, value_type, param_type, remark)
VALUES
    (5001, 'enable_realtime_tool_router', 'true', 'boolean', 1, '是否启用实时AI工具路由'),
    (5002, 'enable_direct_answer_tool', 'true', 'boolean', 1, '工具模式是否启用direct_answer路由'),
    (5003, 'enable_prompt_enhancement', 'true', 'boolean', 1, '是否增强系统提示词上下文'),
    (5004, 'tts_parallel_workers', '1', 'number', 1, 'TTS模型未配置时的默认并行任务数'),
    (5005, 'tts_first_segment_chars', '18', 'number', 1, 'TTS模型未配置时的首段触发字符数'),
    (5006, 'tts_segment_chars', '60', 'number', 1, 'TTS模型未配置时的后续分段字符数')
ON DUPLICATE KEY UPDATE
    param_value = VALUES(param_value),
    value_type = VALUES(value_type),
    param_type = VALUES(param_type),
    remark = VALUES(remark);
