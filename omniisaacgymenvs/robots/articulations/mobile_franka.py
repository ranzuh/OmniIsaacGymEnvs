# Copyright (c) 2021, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

from typing import Optional
import math
import numpy as np
import torch
from omni.isaac.core.robots.robot import Robot
from omni.isaac.core.utils.nucleus import get_assets_root_path
from omni.isaac.core.utils.stage import add_reference_to_stage
from omniisaacgymenvs.tasks.utils.usd_utils import set_drive

from omni.isaac.core.utils.prims import get_prim_at_path
from pxr import PhysxSchema

class MobileFranka(Robot):
    def __init__(
        self,
        prim_path: str,
        name: Optional[str] = "mobilefranka",
        usd_path: Optional[str] = None,
        translation: Optional[torch.tensor] = None,
        orientation: Optional[torch.tensor] = None,
    ) -> None:
        """[summary]
        """

        self._usd_path = usd_path
        self._name = name

        self._position = torch.tensor([0.0, 0.0, 0.0]) if translation is None else translation
        # turn the robot 180 degrees, for some reason it was pointed to wrong direction default orientation: ([0.0, 0.0, 0.0, 1.0])
        self._orientation = torch.tensor([1.0, 0.0, 0.0, 0.0]) if orientation is None else orientation 

        if self._usd_path is None:
            #assets_root_path = get_assets_root_path()
            assets_root_path = "omniverse://localhost/NVIDIA/Assets/Isaac/2022.2.0"
            #if assets_root_path is None:
            #    carb.log_error("Could not find Isaac Sim assets folder")
            #self._usd_path = assets_root_path + "/Isaac/Robots/Franka/franka_instanceable.usd"
            #self._usd_path = assets_root_path + "/Isaac/Robots/Clearpath/RidgebackFranka/ridgeback_franka.usd"
            from pathlib import Path
            current_working_dir = Path.cwd()
            self._usd_path = str(current_working_dir.parent) + "/assets/ridgeback_franka/ridgeback_franka6_instanceable.usd"
            #self._usd_path = "/home/eetu/multi-agent-rl-omni/assets/ridgeback_franka/ridgeback_franka6_instanceable.usd"
            #self._usd_path = "/home/eetu/Desktop/ridgeback_franka6.usd"


        add_reference_to_stage(self._usd_path, prim_path)
        
        super().__init__(
            prim_path=prim_path,
            name=name,
            translation=self._position,
            orientation=self._orientation,
            articulation_controller=None,
        )

        # arm
        dof_paths = [
            "panda_link0/panda_joint1",
            "panda_link1/panda_joint2",
            "panda_link2/panda_joint3",
            "panda_link3/panda_joint4",
            "panda_link4/panda_joint5",
            "panda_link5/panda_joint6",
            "panda_link6/panda_joint7",
            "panda_hand/panda_finger_joint1",
            "panda_hand/panda_finger_joint2"
        ]

        drive_type = ["angular"] * 7 + ["linear"] * 2
        default_dof_pos = [math.degrees(x) for x in [0.0, -1.0, 0.0, -2.2, 0.0, 2.4, 0.8]] + [0.02, 0.02]
        stiffness = [400*np.pi/180] * 7 + [10000] * 2
        damping = [80*np.pi/180] * 7 + [100] * 2
        max_force = [87, 87, 87, 87, 12, 12, 12, 200, 200]
        max_velocity = [math.degrees(x) for x in [2.175, 2.175, 2.175, 2.175, 2.61, 2.61, 2.61]] + [0.2, 0.2]

        for i, dof in enumerate(dof_paths):
            set_drive(
                prim_path=f"{self.prim_path}/{dof}",
                drive_type=drive_type[i],
                target_type="position",
                target_value=default_dof_pos[i],
                stiffness=stiffness[i],
                damping=damping[i],
                max_force=max_force[i]
            )

            PhysxSchema.PhysxJointAPI(get_prim_at_path(f"{self.prim_path}/{dof}")).CreateMaxJointVelocityAttr().Set(max_velocity[i])
        
        # base
        dof_paths = [
            "world/dummy_base_prismatic_x_joint",
            "dummy_base_x/dummy_base_prismatic_y_joint",
            "dummy_base_y/dummy_base_revolute_z_joint"
        ]

        drive_type = ["linear"] * 2 + ["angular"]
        default_dof_pos = [0.0] * 3
        stiffness = [0.0] * 3
        damping = [1000000.0] * 3
        max_force = [4800.0] * 3

        for i, dof in enumerate(dof_paths):
            set_drive(
                prim_path=f"{self.prim_path}/{dof}",
                drive_type=drive_type[i],
                target_type="velocity",
                target_value=default_dof_pos[i],
                stiffness=stiffness[i],
                damping=damping[i],
                max_force=max_force[i]
            )
        
