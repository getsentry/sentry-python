#!/usr/bin/env bash

# Create python file
python -m grpc_tools.protoc --proto_path=./protos --python_out=. --pyi_out=. --grpc_python_out=. ./protos/grpc_test_service.proto

# Fix imports in generated file
sed -i '' 's/import grpc_/import tests\.integrations\.grpc\.grpc_/g' ./grpc_test_service_pb2_grpc.py
