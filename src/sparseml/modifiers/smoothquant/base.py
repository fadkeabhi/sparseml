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


from typing import List, Optional, Tuple

from sparseml.core import Modifier
from sparseml.core.state import State


__all__ = ["SmoothQuantModifier"]


class SmoothQuantModifier(Modifier):
    """ """

    migration_strength: float
    mappings: List[Tuple]
    ignore: Optional[List[str]] = None
    logarithmic_equalization: Optional[bool] = False
    num_calibration_steps: Optional[int] = None

    def on_initialize_structure(self, state: "State", **kwargs):
        pass  # nothing needed for this modifier
