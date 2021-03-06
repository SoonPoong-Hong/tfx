# Copyright 2020 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Portable library for registering and publishing executions."""
from typing import Mapping, MutableMapping, Optional, Sequence

from tfx import types
from tfx.orchestration import metadata
from tfx.orchestration.portable.mlmd import execution_lib
from tfx.proto.orchestration import execution_result_pb2

from ml_metadata.proto import metadata_store_pb2


def publish_cached_execution(
    metadata_handler: metadata.Metadata,
    contexts: Sequence[metadata_store_pb2.Context],
    execution_id: int,
    output_artifacts: Optional[MutableMapping[str,
                                              Sequence[types.Artifact]]] = None,
) -> None:
  """Marks an existing execution as using cached outputs from a previous execution.

  Args:
    metadata_handler: A handler to access MLMD.
    contexts: MLMD contexts to associated with the execution.
    execution_id: The id of the execution.
    output_artifacts: Output artifacts of the execution. Each artifact will be
      linked with the execution through an event with type OUTPUT.
  """
  [execution] = metadata_handler.store.get_executions_by_id([execution_id])
  execution.last_known_state = metadata_store_pb2.Execution.CACHED

  execution_lib.put_execution(
      metadata_handler,
      execution,
      contexts,
      input_artifacts=None,
      output_artifacts=output_artifacts)


def publish_succeeded_execution(
    metadata_handler: metadata.Metadata,
    execution_id: int,
    contexts: Sequence[metadata_store_pb2.Context],
    output_artifacts: Optional[MutableMapping[str,
                                              Sequence[types.Artifact]]] = None,
    executor_output: Optional[execution_result_pb2.ExecutorOutput] = None
) -> None:
  """Marks an existing execution as success.

  Also publishes the output artifacts produced by the execution. This method
  will also merge the executor produced info into system generated output
  artifacts. The `last_know_state` of the execution will be changed to
  `COMPLETE` and the output artifacts will be marked as `LIVE`.

  Args:
    metadata_handler: A handler to access MLMD.
    execution_id: The id of the execution to mark successful.
    contexts: MLMD contexts to associated with the execution.
    output_artifacts: Output artifacts skeleton of the execution, generated by
      the system. Each artifact will be linked with the execution through an
      event with type OUTPUT.
    executor_output: Executor outputs. `executor_output.output_artifacts` will
      be used to update system-generated output artifacts passed in through
      `output_artifacts` arg. There are three contraints to the update: 1. The
        keys in `executor_output.output_artifacts` are expected to be a subset
        of the system-generated output artifacts dict. 2. An update to a certain
        key should contains all the artifacts under that key. 3. An update to an
        artifact should not change the type of the artifact.

  Raises:
    RuntimeError: if the executor output to a output channel is partial.
  """
  output_artifacts = output_artifacts or {}
  if executor_output:
    if not set(executor_output.output_artifacts.keys()).issubset(
        output_artifacts.keys()):
      raise RuntimeError(
          'Executor output %s contains more keys than output skeleton %s.' %
          (executor_output, output_artifacts))
    for key, artifact_list in output_artifacts.items():
      if key not in executor_output.output_artifacts:
        continue
      updated_artifact_list = executor_output.output_artifacts[key].artifacts
      if len(artifact_list) != len(updated_artifact_list):
        raise RuntimeError(
            'Partially update an output channel is not supported')

      for original, updated in zip(artifact_list, updated_artifact_list):
        if original.type_id != updated.type_id:
          raise RuntimeError('Executor output should not change artifact type.')
        original.mlmd_artifact.CopyFrom(updated)

  # Marks output artifacts as LIVE.
  for artifact_list in output_artifacts.values():
    for artifact in artifact_list:
      artifact.mlmd_artifact.state = metadata_store_pb2.Artifact.LIVE

  [execution] = metadata_handler.store.get_executions_by_id([execution_id])
  execution.last_known_state = metadata_store_pb2.Execution.COMPLETE

  execution_lib.put_execution(
      metadata_handler, execution, contexts, output_artifacts=output_artifacts)


def publish_failed_execution(metadata_handler: metadata.Metadata,
                             contexts: Sequence[metadata_store_pb2.Context],
                             execution_id: int) -> None:
  """Marks an existing execution as failed.

  Args:
    metadata_handler: A handler to access MLMD.
    contexts: MLMD contexts to associated with the execution.
    execution_id: The id of the execution.
  """
  [execution] = metadata_handler.store.get_executions_by_id([execution_id])
  execution.last_known_state = metadata_store_pb2.Execution.FAILED

  execution_lib.put_execution(metadata_handler, execution, contexts)


def publish_internal_execution(
    metadata_handler: metadata.Metadata,
    contexts: Sequence[metadata_store_pb2.Context],
    execution_id: int,
    output_artifacts: Optional[MutableMapping[str,
                                              Sequence[types.Artifact]]] = None
) -> None:
  """Marks an exeisting execution as as success and links its output to an INTERNAL_OUTPUT event.

  Args:
    metadata_handler: A handler to access MLMD.
    contexts: MLMD contexts to associated with the execution.
    execution_id: The id of the execution.
    output_artifacts: Output artifacts of the execution. Each artifact will be
      linked with the execution through an event with type INTERNAL_OUTPUT.
  """
  [execution] = metadata_handler.store.get_executions_by_id([execution_id])
  execution.last_known_state = metadata_store_pb2.Execution.COMPLETE

  execution_lib.put_execution(
      metadata_handler,
      execution,
      contexts,
      output_artifacts=output_artifacts,
      output_event_type=metadata_store_pb2.Event.INTERNAL_OUTPUT)


def register_execution(
    metadata_handler: metadata.Metadata,
    execution_type: metadata_store_pb2.ExecutionType,
    contexts: Sequence[metadata_store_pb2.Context],
    input_artifacts: Optional[MutableMapping[str,
                                             Sequence[types.Artifact]]] = None,
    exec_properties: Optional[Mapping[str, types.Property]] = None,
) -> metadata_store_pb2.Execution:
  """Registers a new execution in MLMD.

  Along with the execution:
  -  the input artifacts will be linked to the execution.
  -  the contexts will be linked to both the execution and its input artifacts.

  Args:
    metadata_handler: A handler to access MLMD.
    execution_type: The type of the execution.
    contexts: MLMD contexts to associated with the execution.
    input_artifacts: Input artifacts of the execution. Each artifact will be
      linked with the execution through an event.
    exec_properties: Execution properties. Will be attached to the execution.

  Returns:
    An MLMD execution that is registered in MLMD, with id populated.
  """
  execution = execution_lib.prepare_execution(
      metadata_handler, execution_type, metadata_store_pb2.Execution.RUNNING,
      exec_properties)
  return execution_lib.put_execution(
      metadata_handler, execution, contexts, input_artifacts=input_artifacts)
