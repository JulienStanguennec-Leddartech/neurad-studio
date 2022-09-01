# Copyright 2022 The Plenoptix Team. All rights reserved.
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

"""
Abstracts for the Pipeline class.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional

from torch import nn
from torch.nn import Parameter

from nerfactory.configs import base as cfg
from nerfactory.dataloaders.base import Dataloader
from nerfactory.models.base import Model
from nerfactory.utils import profiler
from nerfactory.utils.callbacks import TrainingCallback, TrainingCallbackAttributes


class Pipeline(nn.Module):
    """The intent of this class is to provide a higher level interface for the Model
    that will be easy to use for our Trainer class.

    This class will contain high level functions for the model like getting the loss
    dictionaries and visualization code. It should have ways to get the next iterations
    training loss, evaluation loss, and generate whole images for visualization. Each model
    class should be 1:1 with a pipeline that can act as a standardized interface and hide
    differences in how each model takes in and outputs data.

    This class's function is to hide the dataloader and model classes from the trainer,
    worrying about:
    1) Fetching data with the dataloader
    2) Feeding the model the data and fetching the loss
    Hopefully this provides a higher level interface for the trainer to use, and
    simplifying the model classes, which each may have different forward() methods
    and so on.


    TODO: For visualizer functionality to be added down the line, we should make sure
    that we are still abstracting away the Model from the end user. The visualizer function
    should probably be done by adding a new iterator on the base dataloader that will
    talk to the actual visualizer. This is probably ideal to have the visualizer be
    primarily located in the dataloader (first because it makes sense as the
    visualizers main job in this context is to feed in data for the model to load)
    so that we can have an easier time ensuring that the visualizer is always
    returning the same formatted data as for in train / eval. All this is pending changes to
    be done in the future... but just bear in mind that if learned parameters are in the dataloader,
    the visualizer may have to use those parameters as well.


    Args:
        model: The model to be used in the pipeline.
        dataloader: The dataloader to be used in the pipeline.
        loss_coefficients: A dictionary of loss coefficients that will be used

    Attributes:
        self.dataloader (Dataloader): The dataloader that will be used
        self.model (Model): The model that will be used
    """

    def __init__(self, config: cfg.PipelineConfig, device: str, test_mode: bool = False):
        super().__init__()
        self.dataloader: Dataloader = config.dataloader.setup(device=device, test_mode=test_mode)
        self.dataloader.to(device)
        # TODO(ethan): get rid of scene_bounds from the model
        assert self.dataloader.train_datasetinputs is not None, "Missing DatasetInputs"
        self.model: Model = config.model.setup(scene_bounds=self.dataloader.train_datasetinputs.scene_bounds)
        self.model.to(device)

    @property
    def device(self):
        """Returns the device that the model is on."""
        return self.model.device

    @abstractmethod
    @profiler.time_function
    def get_train_loss_dict(self, step: Optional[int] = None):
        """This function gets your training loss dict. This will be responsible for
        getting the next batch of data from the dataloader and interfacing with the
        Model class, feeding the data to the model's forward function."""
        ray_bundle, batch = self.dataloader.next_train()
        model_outputs, loss_dict, metrics_dict = self.model(ray_bundle, batch)
        return model_outputs, loss_dict, metrics_dict

    @abstractmethod
    @profiler.time_function
    def get_eval_loss_dict(self, step: Optional[int] = None):
        """This function gets your evaluation loss dict. It needs to get the data
        from the dataloader and feed it to the model's forward function"""
        self.eval()
        # NOTE(ethan): next_eval() is not being used right now
        assert self.dataloader.eval_dataloader is not None
        for camera_ray_bundle, batch in self.dataloader.eval_dataloader:
            assert camera_ray_bundle.camera_indices is not None
            image_idx = int(camera_ray_bundle.camera_indices[0, 0])
            outputs = self.model.get_outputs_for_camera_ray_bundle(camera_ray_bundle)
            psnr = self.model.log_test_image_outputs(image_idx, step, batch, outputs)
        # TODO(ethan): this function should probably return something?
        self.train()

    @abstractmethod
    def log_test_image_outputs(self) -> None:
        """Log the test image outputs"""

    def load_pipeline(self, loaded_state: Dict[str, Any]) -> None:
        """Load the checkpoint from the given path"""
        state = {key.replace("module.", ""): value for key, value in loaded_state.items()}
        self.load_state_dict(state)  # type: ignore

    def get_training_callbacks(
        self, training_callback_attributes: TrainingCallbackAttributes
    ) -> List[TrainingCallback]:
        """Returns the training callbacks from both the Dataloader and the Model."""
        dataloader_callbacks = self.dataloader.get_training_callbacks(training_callback_attributes)
        model_callbacks = self.model.get_training_callbacks(training_callback_attributes)
        callbacks = dataloader_callbacks + model_callbacks
        return callbacks

    def get_param_groups(self) -> Dict[str, List[Parameter]]:
        """Get the param groups for the pipeline.

        Returns:
            A list of dictionaries containing the pipeline's param groups.
        """
        dataloader_params = self.dataloader.get_param_groups()
        model_params = self.model.get_param_groups()
        # TODO(ethan): assert that key names don't overlap
        return {**dataloader_params, **model_params}