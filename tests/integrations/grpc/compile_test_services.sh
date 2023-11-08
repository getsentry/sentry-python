#!/usr/bin/env bash

# Run this script from the project root to generate the python code

TARGET_PATH=./tests/integrations/grpc

# Create python file
python -m grpc_tools.protoc \
    --proto_path=$TARGET_PATH/protos/ \
    --python_out=$TARGET_PATH/ \
    --pyi_out=$TARGET_PATH/ \
    --grpc_python_out=$TARGET_PATH/ \
    $TARGET_PATH/protos/grpc_test_service.proto

echo Code generation successfull
