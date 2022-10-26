# Copyright (c) 2021 - present / Neuralmagic, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Modifier for performing knowledge distillation via feature imitation.
"""

import logging
from typing import Any, List, Optional, Union, Mapping

import torch
from torch.nn import Module

from sparseml.optim import ModifierProp
from sparseml.pytorch.sparsification.distillation.modifier_distillation_base import (
    BaseDistillationModifier,
)
from sparseml.pytorch.sparsification.modifier import PyTorchModifierYAML
from sparseml.pytorch.utils import BaseLogger


__all__ = [
    "PerLayerDistillationModifier",
]

_LOGGER = logging.getLogger(__name__)

_DISTILLATION_TYPES = [
    torch.nn.Conv1d,
    torch.nn.Conv2d,
    torch.nn.Conv3d,
    torch.nn.Linear,
]


@PyTorchModifierYAML()
class PerLayerDistillationModifier(BaseDistillationModifier):
    """
    Adds a knowledge distillation loss based on the feature imitation loss.
    A distillation_teacher module may be provided as a kwarg to
    the Manager initialization and loss_update(loss) must be called before any
    backwards pass in the integrated training flow.
    If no teacher model is provided, then self-distillation will be used.
    The feature difference between teacher and student can be weighted spatially
    by a weighing function.

    | Sample yaml:
    |   !PerLayerDistillationModifier
    |       start_epoch: 0.0
    |       gain: 2.0
    |       number_of_classes: 80
    |       student_features: [64, 128, 256]
    |       teacher_features: [128, 256, 512]

    :param start_epoch: The epoch to start the modifier at
    :param end_epoch: The epoch to end the modifier at
    :param distill_output_keys: List of keys for the module outputs to use for
        distillation if multiple outputs are present. None or empty list defaults
        to using all available outputs
    :param teacher_input_keys: List of keys to filter the inputs by before
        passing into the teacher. None or empty list defaults to using
        all available inputs
    :param update_frequency:
    :param gain: How much to weight the distillation loss. Default is 1.5
    :param normalize: Whether to normalize the output difference by the
        the magnitude of the teacher's output
    """

    def __init__(
        self,
        gain: float,
        start_epoch: float = -1.0,
        end_epoch: float = -1.0,
        distill_output_keys: Optional[List[Any]] = None,
        teacher_input_keys: Optional[List[Any]] = None,
        update_frequency: float = -1.0,
        normalize: bool = True,
        student_names: Optional[List[str]] = None,
        teacher_names: Optional[List[str]] = None,
        project_features: bool = False,
        project_from: str = "teacher",
    ):
        super().__init__(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            distill_output_keys=distill_output_keys,
            teacher_input_keys=teacher_input_keys,
            update_frequency=update_frequency,
        )
        self.gain = gain
        self.normalize = normalize
        self.student_names = student_names
        self.teacher_names = teacher_names
        self.project_features = project_features
        self.project_from = project_from
        self._cached_student_output = None
        self._cached_teacher_output = None
        self._student_handles = None
        self._teacher_handles = None
        self._projection = None
        self._student_output_shapes = None
        self._teacher_output_shapes = None

    @ModifierProp()
    def gain(self) -> float:
        """
        :return: how much to weight the distillation loss vs the base loss
            (e.g. hardness of 0.6 will return 0.6 * distill_loss + 0.4 * base_loss)
        """
        return self._gain

    @gain.setter
    def gain(self, value: float):
        """
        :params value: how much to weight the distillation loss vs the base loss
            (e.g. hardness of 0.6 will return 0.6 * distill_loss + 0.4 * base_loss)
        """
        self._gain = value

    @ModifierProp()
    def normalize(self) -> bool:
        """
        :return: whether to normalize distillation loss by magnitude of teacher output
        """
        return self._normalize

    @normalize.setter
    def normalize(self, value: bool):
        """
        :params value: whether to normalize distillation loss
            by magnitude of teacher output
        """
        self._normalize = value

    @ModifierProp()
    def student_names(self) -> List[str]:
        return self._student_names

    @student_names.setter
    def student_names(self, value: List[str]):
        self._student_names = value

    @ModifierProp()
    def teacher_names(self) -> List[str]:
        if self._teacher_names is not None:
            return self._teacher_names
        elif self.student_names is not None:
            return self.student_names
        else:
            return None

    @teacher_names.setter
    def teacher_names(self, value: List[str]):
        self._teacher_names = value

    @ModifierProp()
    def project_features(self) -> bool:
        return self._project_features

    @project_features.setter
    def project_features(self, value: bool):
        self._project_features = value

    @ModifierProp()
    def project_from(self) -> str:
        return self._project_from

    @project_from.setter
    def project_from(self, value: str):
        self._project_from = value

    def initialize(
        self,
        module: Module,
        epoch: float = 0,
        loggers: Optional[List[BaseLogger]] = None,
        distillation_teacher: Union[Module, str] = "disable",
        **kwargs,
    ):
        """
        Store the teacher model for distillation if provided

        :param module: the PyTorch model/module to modify
        :param epoch: The epoch to initialize the modifier and module at.
            Defaults to 0 (start of the training process)
        :param loggers: Optional list of loggers to log the modification process to
        :param distillation_teacher: teacher module to perform knowledge distillation
            with. If not provided, self distillation will be used with a teacher
             from a copy of the given module at the start epoch. If given string
             "disable" this modifier will not apply distillation of any kind,
             even in the active epoch range
        :param kwargs: Optional kwargs to support specific arguments
            for individual modifiers.
        """
        super().initialize(module, epoch, loggers, distillation_teacher, **kwargs)

        if isinstance(distillation_teacher, Module):
            self._cached_student_output = {}
            self._cached_teacher_output = {}
            self._student_output_shapes = {}
            self._teacher_output_shapes = {}

            cached_student_layers = {}
            cached_teacher_layers = {}

            if self.student_names is None:
                _find_layers_by_type(module, cached_student_layers)
                _find_layers_by_type(distillation_teacher, cached_teacher_layers)

                self._student_names = list(cached_student_layers.keys())
                self._teacher_names = list(cached_teacher_layers.keys())
            else:
                _find_layers_by_name(module, self.student_names, cached_student_layers)
                _find_layers_by_name(distillation_teacher, self.teacher_names, cached_teacher_layers)

            self._student_handles = []
            self._teacher_handles = []
            for layer_name in cached_student_layers:
                self._student_handles.append(
                    cached_student_layers[layer_name].register_forward_hook(
                        _create_cache_output_hook(layer_name, self._cached_student_output, self._student_output_shapes)
                    )
                )

            for layer_name in cached_teacher_layers:
                self._student_handles.append(
                    cached_teacher_layers[layer_name].register_forward_hook(
                        _create_cache_output_hook(layer_name, self._cached_teacher_output, self._teacher_output_shapes)
                    )
                )
            self._teacher = distillation_teacher
        else:
            raise ValueError(
                "unrecognized value for distillation_modifier given of "
                f"{distillation_teacher}. "
                "To disable set to 'disable' and for self attention set to 'self'"
            )

    def finalize(
        self, module: Optional[Module] = None, reset_loggers: bool = True, **kwargs
    ):
        """
        Cleans up any state and hooks

        :param module: The model/module to finalize the modifier for.
            Marked optional so state can still be cleaned up on delete,
            but generally should always be passed in.
        :param reset_loggers: True to remove any currently attached loggers (default),
            False to keep the loggers attached.
        :param kwargs: Optional kwargs to support specific arguments
            for individual modifiers.
        """
        super().finalize(module, reset_loggers, **kwargs)
        for handle in self.student_handles:
            handle.remove()
        for handle in self.teacher_handles:
            handle.remove()
        self._student_handles = None
        self._teacher_handles = None
        self._cached_student_output = None
        self._cached_teacher_output = None

    def compute_distillation_loss(self, **kwargs):
        distillation_loss = 0.0

        if self.project_features and self._projection is None:
            self._initialize_projection()

        for index in range(len(self.student_names)):
            student_module_output = self._cached_student_output[self.student_names[index]]
            teacher_module_output = self._cached_teacher_output[self.teacher_names[index]]

            if self.project_features:
                self._projection[index] = self._projection[index].to(student_module_output.device)
                self._projection[index] = self._projection[index].to(student_module_output.dtype)
                if self.project_from == 'teacher':
                    teacher_module_output = self._projection[index](teacher_module_output)
                else:
                    student_module_output = self._projection[index](student_module_output)

            output_difference = torch.mean(
                (student_module_output - teacher_module_output) ** 2,
            )

            if self.normalize:
                teacher_output_magnitude = torch.mean(teacher_module_output ** 2)
                output_difference /= teacher_output_magnitude

            distillation_loss += output_difference

        return distillation_loss

    def _initialize_projection(self):
        self._projection = []
        for index in range(len(self.student_names)):
            student_shape = self._student_output_shapes[self.student_names[index]]
            teacher_shape = self._teacher_output_shapes[self._teacher[index]]
            if len(student_shape) == 4:
                student_features = student_shape[1]
                teacher_features = teacher_shape[1]
                if self.project_from == 'teacher':
                    self._projection.append(
                        torch.nn.Conv2d(
                            in_channels=teacher_features,
                            out_channels=student_features,
                            kernel_size=1,
                            bias=False,
                        )
                    )
                else:
                    self._projection.append(
                        torch.nn.Conv2d(
                            in_channels=student_features,
                            out_channels=teacher_features,
                            kernel_size=1,
                            bias=False,
                        )
                    )
            else:
                student_features = student_shape[-1]
                teacher_features = teacher_shape[-1]
                if self.project_from == 'teacher':
                    self._projection.append(
                        torch.nn.Linear(
                            in_features=teacher_features,
                            out_features=student_features,
                            bias=False,
                        )
                    )
                else:
                    self._projection.append(
                        torch.nn.Conv2d(
                            in_features=student_features,
                            out_features=teacher_features,
                            bias=False,
                        )
                    )

    def compute_total_loss(self, loss, distillation_loss):
        return loss + self.gain * distillation_loss


def _create_cache_output_hook(layer_name, outputs, outputs_shape):
    def forward_hook_fn(layer, inp, out):
        outputs[layer_name] = out
        if layer_name not in outputs_shape:
            outputs_shape[layer_name] = out.shape

    return forward_hook_fn


def _find_layers_by_type(layer_module, cached_layers, name=""):
    if type(layer_module) in _DISTILLATION_TYPES:
        cached_layers[name] = layer_module
    for layer_module, child in layer_module.named_children():
        _find_layers_by_type(
            child,
            cached_layers,
            name + "." + layer_module if name != "" else layer_module,
        )


def _find_layers_by_name(layer_module, layer_names, cached_layers, name=""):
    if name in layer_names:
        cached_layers[name] = layer_module
    for layer_module, child in layer_module.named_children():
        _find_layers_by_name(
            child,
            layer_names,
            cached_layers,
            name + "." + layer_module if name != "" else layer_module,
        )
