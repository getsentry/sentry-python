#!/usr/bin/env bash

# Create python files
python -m grpc_tools.protoc --proto_path=./protos --python_out=. --pyi_out=. --grpc_python_out=. ./protos/grpc_test_service.proto
python -m grpc_tools.protoc --proto_path=./protos --python_out=. --pyi_out=. --grpc_python_out=. ./protos/grpc_aio_test_service.proto

# Fix imports in generated files
sed -i '' 's/import grpc_/import tests\.integrations\.grpc\.grpc_/g' ./grpc_test_service_pb2_grpc.py
sed -i '' 's/import grpc_/import tests\.integrations\.grpc\.grpc_/g' ./grpc_aio_test_service_pb2_grpc.py
