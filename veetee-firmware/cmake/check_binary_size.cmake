if(NOT DEFINED IMAGE OR NOT DEFINED MAX_BYTES OR NOT EXISTS "${IMAGE}")
    message(FATAL_ERROR "IMAGE and MAX_BYTES must identify an existing binary")
endif()

file(SIZE "${IMAGE}" IMAGE_BYTES)
math(EXPR MAX_BYTES_DECIMAL "${MAX_BYTES}")
if(IMAGE_BYTES GREATER MAX_BYTES_DECIMAL)
    message(FATAL_ERROR
        "${IMAGE} is ${IMAGE_BYTES} bytes, exceeding partition capacity ${MAX_BYTES_DECIMAL}")
endif()

message(STATUS
    "Resource image size ${IMAGE_BYTES}/${MAX_BYTES_DECIMAL} bytes")
