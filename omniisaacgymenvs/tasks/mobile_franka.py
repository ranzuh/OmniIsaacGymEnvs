# Copyright (c) 2021, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

from omniisaacgymenvs.tasks.base.rl_task import RLTask
from omniisaacgymenvs.robots.articulations.franka import Franka
from omniisaacgymenvs.robots.articulations.mobile_franka import MobileFranka
from omniisaacgymenvs.robots.articulations.cabinet import Cabinet
from omniisaacgymenvs.robots.articulations.views.franka_view import FrankaView
from omniisaacgymenvs.robots.articulations.views.cabinet_view import CabinetView
from omniisaacgymenvs.robots.articulations.views.mobile_franka_view import MobileFrankaView

from omni.isaac.core.objects import DynamicCuboid, FixedCuboid
from omni.isaac.core.prims import RigidPrim, RigidPrimView
from omni.isaac.core.utils.prims import get_prim_at_path
from omni.isaac.core.utils.stage import get_current_stage
from omni.isaac.core.utils.torch.transformations import *

from omni.isaac.cloner import Cloner

import numpy as np
import torch
import math

from pxr import Usd, UsdGeom


class MobileFrankaTask(RLTask):
    def __init__(
        self,
        name,
        sim_config,
        env,
        offset=None
    ) -> None:
        self._sim_config = sim_config
        self._cfg = sim_config.config
        self._task_cfg = sim_config.task_config

        self._num_envs = self._task_cfg["env"]["numEnvs"]
        self._env_spacing = self._task_cfg["env"]["envSpacing"]

        self._max_episode_length = self._task_cfg["env"]["episodeLength"]

        self.action_scale = self._task_cfg["env"]["actionScale"]
        self.start_position_noise = self._task_cfg["env"]["startPositionNoise"]
        self.start_rotation_noise = self._task_cfg["env"]["startRotationNoise"]
        self.num_props = 1 #self._task_cfg["env"]["numProps"]

        self.dof_vel_scale = self._task_cfg["env"]["dofVelocityScale"]
        self.dist_reward_scale = self._task_cfg["env"]["distRewardScale"]
        self.rot_reward_scale = self._task_cfg["env"]["rotRewardScale"]
        self.around_handle_reward_scale = self._task_cfg["env"]["aroundHandleRewardScale"]
        self.open_reward_scale = self._task_cfg["env"]["openRewardScale"]
        self.finger_dist_reward_scale = self._task_cfg["env"]["fingerDistRewardScale"]
        self.action_penalty_scale = self._task_cfg["env"]["actionPenaltyScale"]
        self.finger_close_reward_scale = self._task_cfg["env"]["fingerCloseRewardScale"]

        self.distX_offset = 0.04
        #self.dt = 1/60.
        control_frequency = 120.0 / self._task_cfg["env"]["controlFrequencyInv"] # 30
        self.dt = 1/control_frequency

        self._num_observations = 18 #23
        self._num_actions = 12

        RLTask.__init__(self, name, env)
        return

    def set_up_scene(self, scene) -> None:

        self.get_franka()
        #self.get_cabinet()
        
        super().set_up_scene(scene, replicate_physics=False)

        self._mobilefrankas = MobileFrankaView(prim_paths_expr="/World/envs/.*/mobile_franka", name="franka_view")
        #self._cabinets = CabinetView(prim_paths_expr="/World/envs/.*/cabinet", name="cabinet_view")

        scene.add(self._mobilefrankas)
        #scene.add(self._frankas._hands)
        #scene.add(self._frankas._lfingers)
        #scene.add(self._frankas._rfingers)
        #scene.add(self._cabinets)
        #scene.add(self._cabinets._drawers)

        self.init_data()
        return

    def get_franka(self):
        mobile_franka = MobileFranka(prim_path=self.default_zero_env_path + "/mobile_franka", name="mobile_franka")
        self._sim_config.apply_articulation_settings("franka", get_prim_at_path(mobile_franka.prim_path), self._sim_config.parse_actor_config("franka"))     

    def init_data(self) -> None:
        def get_env_local_pose(env_pos, xformable, device):
            """Compute pose in env-local coordinates"""
            world_transform = xformable.ComputeLocalToWorldTransform(0)
            world_pos = world_transform.ExtractTranslation()
            world_quat = world_transform.ExtractRotationQuat()
            
            px = world_pos[0] - env_pos[0]
            py = world_pos[1] - env_pos[1]
            pz = world_pos[2] - env_pos[2]
            qx = world_quat.imaginary[0]
            qy = world_quat.imaginary[1]
            qz = world_quat.imaginary[2]
            qw = world_quat.real

            return torch.tensor([px, py, pz, qw, qx, qy, qz], device=device, dtype=torch.float)

        stage = get_current_stage()
        hand_pose = get_env_local_pose(self._env_pos[0], UsdGeom.Xformable(stage.GetPrimAtPath("/World/envs/env_0/mobile_franka/panda_link7")), self._device)
        lfinger_pose = get_env_local_pose(
            self._env_pos[0], UsdGeom.Xformable(stage.GetPrimAtPath("/World/envs/env_0/mobile_franka/panda_leftfinger")), self._device
        )
        rfinger_pose = get_env_local_pose(
            self._env_pos[0], UsdGeom.Xformable(stage.GetPrimAtPath("/World/envs/env_0/mobile_franka/panda_rightfinger")), self._device
        )

        finger_pose = torch.zeros(7, device=self._device)
        finger_pose[0:3] = (lfinger_pose[0:3] + rfinger_pose[0:3]) / 2.0
        finger_pose[3:7] = lfinger_pose[3:7]
        hand_pose_inv_rot, hand_pose_inv_pos = (tf_inverse(hand_pose[3:7], hand_pose[0:3]))

        grasp_pose_axis = 1
        franka_local_grasp_pose_rot, franka_local_pose_pos = tf_combine(hand_pose_inv_rot, hand_pose_inv_pos, finger_pose[3:7], finger_pose[0:3])
        franka_local_pose_pos += torch.tensor([0, 0.04, 0], device=self._device)
        self.franka_local_grasp_pos = franka_local_pose_pos.repeat((self._num_envs, 1))
        self.franka_local_grasp_rot = franka_local_grasp_pose_rot.repeat((self._num_envs, 1))

        # drawer_local_grasp_pose = torch.tensor([0.3, 0.01, 0.0, 1.0, 0.0, 0.0, 0.0], device=self._device)
        # self.drawer_local_grasp_pos = drawer_local_grasp_pose[0:3].repeat((self._num_envs, 1))
        # self.drawer_local_grasp_rot = drawer_local_grasp_pose[3:7].repeat((self._num_envs, 1))

        # self.gripper_forward_axis = torch.tensor([0, 0, 1], device=self._device, dtype=torch.float).repeat((self._num_envs, 1))
        # self.drawer_inward_axis = torch.tensor([-1, 0, 0], device=self._device, dtype=torch.float).repeat((self._num_envs, 1))
        # self.gripper_up_axis = torch.tensor([0, 1, 0], device=self._device, dtype=torch.float).repeat((self._num_envs, 1))
        # self.drawer_up_axis = torch.tensor([0, 0, 1], device=self._device, dtype=torch.float).repeat((self._num_envs, 1))

        # self.franka_default_dof_pos = torch.tensor(
        #     [1.157, -1.066, -0.155, -2.239, -1.841, 1.003, 0.469, 0.035, 0.035], device=self._device
        # )
        #[0.00017897569768976496, -0.7856239589326517, 1.8711715534358575e-05, -2.3559680300009447, -5.8659626880341875e-06, 1.5717616294461347, 0.7853945309207777]
        self.mobile_franka_default_dof_pos = torch.tensor(
            [0.0, 0.0, 0.0, 0.0, -0.7856, 0.0, -2.356, 0.0, 1.572, 0.7854, 0.035, 0.035], device=self._device
        )

        self.actions = torch.zeros((self._num_envs, self.num_actions), device=self._device)

    def get_observations(self) -> dict:
        #hand_pos, hand_rot = self._frankas._hands.get_world_poses(clone=False)
        #drawer_pos, drawer_rot = self._cabinets._drawers.get_world_poses(clone=False)
        franka_dof_pos = self._mobilefrankas.get_joint_positions(clone=False)
        #franka_dof_vel = self._frankas.get_joint_velocities(clone=False)
        #self.cabinet_dof_pos = self._cabinets.get_joint_positions(clone=False)
        #self.cabinet_dof_vel = self._cabinets.get_joint_velocities(clone=False)
        self.franka_dof_pos = franka_dof_pos

        # self.franka_grasp_rot, self.franka_grasp_pos, self.drawer_grasp_rot, self.drawer_grasp_pos = self.compute_grasp_transforms(
        #     hand_rot,
        #     hand_pos,
        #     self.franka_local_grasp_rot,
        #     self.franka_local_grasp_pos,
        #     drawer_rot,
        #     drawer_pos,
        #     self.drawer_local_grasp_rot,
        #     self.drawer_local_grasp_pos,
        # )

        #self.franka_lfinger_pos, self.franka_lfinger_rot = self._frankas._lfingers.get_world_poses(clone=False)
        #self.franka_rfinger_pos, self.franka_rfinger_rot = self._frankas._lfingers.get_world_poses(clone=False)

        # dof_pos_scaled = (
        #     2.0
        #     * (franka_dof_pos - self.franka_dof_lower_limits)
        #     / (self.franka_dof_upper_limits - self.franka_dof_lower_limits)
        #     - 1.0
        # )

        # prop_pos = self._props.get_world_poses(clone=False)[0]
        # #print("prop_pos", prop_pos)
        #print("hand_pos", hand_pos)

        # use prop pos as target
        # self.to_target = prop_pos - hand_pos

        # use goal pos as target
        #print(self.default_prop_pos)
        #self.to_target = self.default_prop_pos - self.franka_rfinger_pos
        #print("to_target", self.to_target)
        
        # self.obs_buf = torch.cat(
        #     (
        #         dof_pos_scaled,
        #         franka_dof_vel * self.dof_vel_scale,
        #         #self.to_target,
        #         #self.cabinet_dof_pos[:, 3].unsqueeze(-1),
        #         #self.cabinet_dof_vel[:, 3].unsqueeze(-1),
        #     ),
        #     dim=-1,
        # )

        #print("dof_pos_scaled", dof_pos_scaled, dof_pos_scaled.shape)
        #print("franka_dof_vel", franka_dof_vel, franka_dof_vel.shape)
        #print("to_target", to_target, to_target.shape)
        #print("self.cabinet_dof_pos[:, 3].unsqueeze(-1)", self.cabinet_dof_pos[:, 3].unsqueeze(-1), self.cabinet_dof_pos[:, 3].unsqueeze(-1).shape)
        #print("self.cabinet_dof_vel[:, 3].unsqueeze(-1)", self.cabinet_dof_vel[:, 3].unsqueeze(-1), self.cabinet_dof_vel[:, 3].unsqueeze(-1).shape)
        
        observations = {
            self._mobilefrankas.name: {
                "obs_buf": self.obs_buf
            }
        }
        return observations

    def pre_physics_step(self, actions) -> None:
        reset_env_ids = self.reset_buf.nonzero(as_tuple=False).squeeze(-1)
        if len(reset_env_ids) > 0:
            self.reset_idx(reset_env_ids)

        self.actions = actions.clone().to(self._device)
        targets = self.franka_dof_targets + self.franka_dof_speed_scales * self.dt * self.actions * self.action_scale
        self.franka_dof_targets[:] = torch.clamp(targets, self.franka_dof_lower_limits, self.franka_dof_upper_limits)
        env_ids_int32 = torch.arange(self._mobilefrankas.count, dtype=torch.int32, device=self._device)

        # TODO REMOVE test them to constantly move forward
        self.actions[:, 0] = 1.0

        action_x = self.actions[:, 0]
        action_y = torch.zeros(self._mobilefrankas.count, device=self._device)
        action_yaw = self.actions[:, 2]

        vel_targets = self._calculate_velocity_targets(action_x, action_y, action_yaw)

        self.franka_dof_targets[:, :3] = self.franka_dof_pos[:, :3]
        #print("self.franka_dof_targets", self.franka_dof_targets)
        self._mobilefrankas.set_joint_position_targets(self.franka_dof_targets, indices=env_ids_int32)
        self._mobilefrankas.set_joint_velocity_targets(vel_targets, joint_indices=torch.tensor([0,1,2]))
        #pass

        #print("self.obs_buf", self.obs_buf)
        #print("actions", actions)
        #print("self.rew_buf", self.rew_buf)
    
    def _calculate_velocity_targets(self, action_x, action_y, action_yaw):
        current_yaw = self.franka_dof_pos[:, 2]
        new_yaw = current_yaw + action_yaw
        new_x = torch.cos(new_yaw) * action_x - torch.sin(new_yaw) * action_y
        new_y = torch.sin(new_yaw) * action_x - torch.cos(new_yaw) * action_y
        
        return torch.transpose(torch.stack([new_x, new_y, action_yaw]), 0, 1)

    def reset_idx(self, env_ids):
        indices = env_ids.to(dtype=torch.int32)
        num_indices = len(indices)

        # reset franka
        pos = torch.clamp(
            self.mobile_franka_default_dof_pos.unsqueeze(0)
            + 0.25 * (torch.rand((len(env_ids), self.num_franka_dofs), device=self._device) - 0.5),
            self.franka_dof_lower_limits,
            self.franka_dof_upper_limits,
        )
        dof_pos = torch.zeros((num_indices, self._mobilefrankas.num_dof), device=self._device)
        dof_vel = torch.zeros((num_indices, self._mobilefrankas.num_dof), device=self._device)
        dof_pos[:, :] = pos
        self.franka_dof_targets[env_ids, :] = pos
        self.franka_dof_pos[env_ids, :] = pos

        self._mobilefrankas.set_joint_position_targets(self.franka_dof_targets[env_ids], indices=indices)
        self._mobilefrankas.set_joint_positions(dof_pos, indices=indices)
        self._mobilefrankas.set_joint_velocities(dof_vel, indices=indices)

        # bookkeeping
        self.reset_buf[env_ids] = 0
        self.progress_buf[env_ids] = 0

    def post_reset(self):
        """setup initial values for dof related things. This is run only once when the environment is initialized."""
        self.num_franka_dofs = self._mobilefrankas.num_dof
        self.franka_dof_pos = torch.zeros((self.num_envs, self.num_franka_dofs), device=self._device)
        dof_limits = self._mobilefrankas.get_dof_limits()
        self.franka_dof_lower_limits = dof_limits[0, :, 0].to(device=self._device)
        self.franka_dof_upper_limits = dof_limits[0, :, 1].to(device=self._device)

        # control the joint speeds with these
        self.franka_dof_speed_scales = torch.ones_like(self.franka_dof_lower_limits)
        self.franka_dof_speed_scales[self._mobilefrankas.gripper_indices] = 0.1
        self.franka_dof_speed_scales[self._mobilefrankas._base_indices] = 0.1
        
        self.franka_dof_targets = torch.zeros(
            (self._num_envs, self.num_franka_dofs), dtype=torch.float, device=self._device
        )

        # randomize all envs
        indices = torch.arange(self._num_envs, dtype=torch.int64, device=self._device)
        self.reset_idx(indices)

    def calculate_metrics(self) -> None:
        # self.rew_buf[:] = self.compute_franka_reward(
        #     self.reset_buf, self.progress_buf, self.actions, self.cabinet_dof_pos,
        #     self.franka_grasp_pos, self.drawer_grasp_pos, self.franka_grasp_rot, self.drawer_grasp_rot,
        #     self.franka_lfinger_pos, self.franka_rfinger_pos,
        #     self.gripper_forward_axis, self.drawer_inward_axis, self.gripper_up_axis, self.drawer_up_axis,
        #     self._num_envs, self.dist_reward_scale, self.rot_reward_scale, self.around_handle_reward_scale, self.open_reward_scale,
        #     self.finger_dist_reward_scale, self.action_penalty_scale, self.distX_offset, self._max_episode_length, self.franka_dof_pos,
        #     self.finger_close_reward_scale,
        # )

        # regularization on the actions (summed for each environment)
        #action_penalty = torch.sum(self.actions ** 2, dim=-1)

        #distance_to_target = torch.norm(self.to_target, p=2, dim=-1) / self.dt
        
        reward = torch.zeros_like(self.rew_buf)
        #reward = reward - self.action_penalty_scale * action_penalty - 0.01 * distance_to_target
        #print("action penalty", action_penalty, "scaled", self.action_penalty_scale * action_penalty)
        #print("distance", distance_to_target, "scaled", 0.01 * distance_to_target)
        self.rew_buf[:] = reward

    def is_done(self) -> None:
        # reset if drawer is open or max length reached
        #self.reset_buf = torch.where(self.cabinet_dof_pos[:, 3] > 0.39, torch.ones_like(self.reset_buf), self.reset_buf)
        #self.reset_buf = torch.where(self.progress_buf >= self._max_episode_length - 1, torch.ones_like(self.reset_buf), self.reset_buf)
        pass

    def compute_grasp_transforms(
        self,
        hand_rot,
        hand_pos,
        franka_local_grasp_rot,
        franka_local_grasp_pos,
        drawer_rot,
        drawer_pos,
        drawer_local_grasp_rot,
        drawer_local_grasp_pos,
    ):

        global_franka_rot, global_franka_pos = tf_combine(
            hand_rot, hand_pos, franka_local_grasp_rot, franka_local_grasp_pos
        )
        global_drawer_rot, global_drawer_pos = tf_combine(
            drawer_rot, drawer_pos, drawer_local_grasp_rot, drawer_local_grasp_pos
        )

        return global_franka_rot, global_franka_pos, global_drawer_rot, global_drawer_pos

    def compute_franka_reward(
        self, reset_buf, progress_buf, actions, cabinet_dof_pos,
        franka_grasp_pos, drawer_grasp_pos, franka_grasp_rot, drawer_grasp_rot,
        franka_lfinger_pos, franka_rfinger_pos,
        gripper_forward_axis, drawer_inward_axis, gripper_up_axis, drawer_up_axis,
        num_envs, dist_reward_scale, rot_reward_scale, around_handle_reward_scale, open_reward_scale,
        finger_dist_reward_scale, action_penalty_scale, distX_offset, max_episode_length, joint_positions, finger_close_reward_scale
    ):
        # type: (Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, int, float, float, float, float, float, float, float, float, Tensor) -> Tuple[Tensor, Tensor]

        # distance from hand to the drawer
        d = torch.norm(franka_grasp_pos - drawer_grasp_pos, p=2, dim=-1)
        dist_reward = 1.0 / (1.0 + d ** 2)
        dist_reward *= dist_reward
        dist_reward = torch.where(d <= 0.02, dist_reward * 2, dist_reward)

        axis1 = tf_vector(franka_grasp_rot, gripper_forward_axis)
        axis2 = tf_vector(drawer_grasp_rot, drawer_inward_axis)
        axis3 = tf_vector(franka_grasp_rot, gripper_up_axis)
        axis4 = tf_vector(drawer_grasp_rot, drawer_up_axis)

        dot1 = torch.bmm(axis1.view(num_envs, 1, 3), axis2.view(num_envs, 3, 1)).squeeze(-1).squeeze(-1)  # alignment of forward axis for gripper
        dot2 = torch.bmm(axis3.view(num_envs, 1, 3), axis4.view(num_envs, 3, 1)).squeeze(-1).squeeze(-1)  # alignment of up axis for gripper
        # reward for matching the orientation of the hand to the drawer (fingers wrapped)
        rot_reward = 0.5 * (torch.sign(dot1) * dot1 ** 2 + torch.sign(dot2) * dot2 ** 2)

        # bonus if left finger is above the drawer handle and right below
        around_handle_reward = torch.zeros_like(rot_reward)
        around_handle_reward = torch.where(franka_lfinger_pos[:, 2] > drawer_grasp_pos[:, 2],
                                           torch.where(franka_rfinger_pos[:, 2] < drawer_grasp_pos[:, 2],
                                                       around_handle_reward + 0.5, around_handle_reward), around_handle_reward)
        # reward for distance of each finger from the drawer
        finger_dist_reward = torch.zeros_like(rot_reward)
        lfinger_dist = torch.abs(franka_lfinger_pos[:, 2] - drawer_grasp_pos[:, 2])
        rfinger_dist = torch.abs(franka_rfinger_pos[:, 2] - drawer_grasp_pos[:, 2])
        finger_dist_reward = torch.where(franka_lfinger_pos[:, 2] > drawer_grasp_pos[:, 2],
                                         torch.where(franka_rfinger_pos[:, 2] < drawer_grasp_pos[:, 2],
                                                     (0.04 - lfinger_dist) + (0.04 - rfinger_dist), finger_dist_reward), finger_dist_reward)


        finger_close_reward = torch.zeros_like(rot_reward)
        finger_close_reward = torch.where(d <=0.03, (0.04 - joint_positions[:, 7]) + (0.04 - joint_positions[:, 8]), finger_close_reward)

        # regularization on the actions (summed for each environment)
        action_penalty = torch.sum(actions ** 2, dim=-1)

        # how far the cabinet has been opened out
        open_reward = cabinet_dof_pos[:, 3] * around_handle_reward + cabinet_dof_pos[:, 3]  # drawer_top_joint

        rewards = dist_reward_scale * dist_reward + rot_reward_scale * rot_reward \
            + around_handle_reward_scale * around_handle_reward + open_reward_scale * open_reward \
            + finger_dist_reward_scale * finger_dist_reward - action_penalty_scale * action_penalty + finger_close_reward * finger_close_reward_scale

        # bonus for opening drawer properly
        rewards = torch.where(cabinet_dof_pos[:, 3] > 0.01, rewards + 0.5, rewards)
        rewards = torch.where(cabinet_dof_pos[:, 3] > 0.2, rewards + around_handle_reward, rewards)
        rewards = torch.where(cabinet_dof_pos[:, 3] > 0.39, rewards + (2.0 * around_handle_reward), rewards)

        # # prevent bad style in opening drawer
        # rewards = torch.where(franka_lfinger_pos[:, 0] < drawer_grasp_pos[:, 0] - distX_offset,
        #                       torch.ones_like(rewards) * -1, rewards)
        # rewards = torch.where(franka_rfinger_pos[:, 0] < drawer_grasp_pos[:, 0] - distX_offset,
        #                       torch.ones_like(rewards) * -1, rewards)

        return rewards
