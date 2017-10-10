import os
import time
import copy
import json
import numpy as np # Used pretty much everywhere.
import matplotlib.pyplot as plt
import threading # Used for time locks to synchronize position data.
import rclpy
# ROS Image message
# from sensor_msgs.msg import Image
# ROS Image message -> OpenCV2 image converter
# from cv_bridge import CvBridge, CvBridgeError
# # OpenCV2 for saving an image
# import cv2
# Instantiate CvBridge
# bridge = CvBridge()

from timeit import default_timer as timer
from scipy import stats
from scipy.interpolate import spline
import geometry_msgs.msg

from os import path
from rclpy.qos import QoSProfile, qos_profile_sensor_data
# from gps.agent.agent import Agent # GPS class needed to inherit from.
# from gps.agent.agent_utils import setup, generate_noise # setup used to get hyperparams in init and generate_noise to get noise in sample.
# from gps.agent.config import AGENT_UR_ROS # Parameters needed for config in __init__.
# from gps.sample.sample import Sample # Used to build a Sample object for each sample taken.
from baselines.agent.utility.general_utils import forward_kinematics, get_ee_points, rotation_from_matrix, \
    get_rotation_matrix,quaternion_from_matrix# For getting points and velocities.
# from gps.algorithm.policy.controller_prior_gmm import ControllerPriorGMM
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint # Used for publishing scara joint angles.
from control_msgs.msg import JointTrajectoryControllerState
from std_msgs.msg import String
# from gps.proto.gps_pb2 import JOINT_ANGLES, JOINT_VELOCITIES, ACTION, END_EFFECTOR_POINTS, \
    # END_EFFECTOR_POINT_JACOBIANS, END_EFFECTOR_POINT_VELOCITIES, END_EFFECTOR_ROTATIONS, IMAGE_FEAT, RGB_IMAGE, NOISE
from baselines.agent.scara_arm.tree_urdf import treeFromFile # For KDL Jacobians
from PyKDL import Jacobian, Chain, ChainJntToJacSolver, JntArray # For KDL Jacobians
from collections import namedtuple
import scipy.ndimage as sp_ndimage
from functools import partial
import PyKDL as kdl
StartEndPoints = namedtuple('StartEndPoints', ['start', 'target'])
class MSG_INVALID_JOINT_NAMES_DIFFER(Exception):
    """Error object exclusively raised by _process_observations."""
    pass

class ROBOT_MADE_CONTACT_WITH_GAZEBO_GROUND_SO_RESTART_ROSLAUNCH(Exception):
    """Error object exclusively raised by reset."""
    pass


class AgentSCARAROS(object):
    """Connects the SCARA actions and Deep Learning algorithms."""

    def __init__(self, agent, init_node=True): #hyperparams, , urdf_path, init_node=True
        """Initialized Agent.
        init_node:   Whether or not to initialize a new ROS node."""

        print("I am in init")
        self._observation_msg = None
    #
    #     # Setup the main node.
        # if init_node:
        print("Init ros node")
        rclpy.init(args=None)
        node = rclpy.create_node('robot_ai_node')
        global node
        self._pub = node.create_publisher(JointTrajectory,'/scara_controller/command')
        # self._callbacks = partial(self._observation_callback, robot_id=0)
        self._sub = node.create_subscription(JointTrajectoryControllerState, '/scara_controller/state', self._observation_callback, qos_profile=qos_profile_sensor_data)
        assert self._sub
        self._time_lock = threading.RLock()
        print("setting time clocks")

        if agent['tree_path'].startswith("/"):
            fullpath = agent['tree_path']
            print(fullpath)
        else:
            fullpath = os.path.join(os.path.dirname(__file__), "assets", agent['tree_path'])
        if not path.exists(fullpath):
            raise IOError("File %s does not exist" % fullpath)

        print("I am in reading the file path: ", fullpath)


        # self._valid_joint_set = [set(hyperparams['joint_order'][ii]) for ii in xrange(self.parallel_num)]
        # self._valid_joint_index = [{joint: index for joint, index in
        #                            enumerate(hyperparams['joint_order'][ii])} for ii in xrange(self.parallel_num)]


        # Initialize a tree structure from the robot urdf.
        # Note that the xacro of the urdf is updated by hand.
        # Then the urdf must be compiled.

        _, self.ur_tree = treeFromFile(agent['tree_path'])
        # Retrieve a chain structure between the base and the start of the end effector.
        self.ur_chain = self.ur_tree.getChain(agent['link_names'][0], agent['link_names'][-1])
        print(self.ur_chain)
    #     # Initialize a KDL Jacobian solver from the chain.
        self.jac_solver = ChainJntToJacSolver(self.ur_chain)
        print(self.jac_solver)
        self._observations_stale = [False for _ in range(1)]
        print("after observations stale")

    #
    #     self._currently_resetting = [False for _ in xrange(self.parallel_num)]
    #     # self._reset_cv = threading.Condition(self._time_lock)
    #
    #     self.condition_demo = [self._hyperparams.get('demo', False) for i in xrange(self._hyperparams['conditions'])]
    #     self.controller_demo = [self._hyperparams.get('demo', False) for i in xrange(self._hyperparams['conditions'])]
    #     self.condition_run_trial_times = [0 for i in range(self._hyperparams['conditions'])]
    #
    #     conds_splited = np.array_split(range(self._hyperparams['conditions']), self.parallel_num)
    #     self._conds = [conds.tolist() for conds in conds_splited if conds.size]
    #     if not self.parallel_on_conditions:
    #         num_samples_splited = np.array_split(range(self._hyperparams['num_samples']), self.parallel_num)
    #         self._samples_idx = [num_samples.tolist() for num_samples in num_samples_splited if num_samples.size]

    #     # self._sub = [rospy.Subscriber("/camera1/image_raw",
    #     #                               Image,
    #     #                               self._callbacks_image[ii]) for ii in xrange(self.parallel_num)]
    #
    #     # self.get_demo_samples()
    #     self.period = self._hyperparams['dt']
    #     self.r = [rospy.Rate(1. / self.period) for _ in xrange(self.parallel_num)]
    #     self.r[0].sleep()
    #
    #
    def _observation_callback(self, message):
        # print("Trying to call observation msgs")
        # global _observation_msg
        self._observation_msg =  message
        # print(self._observation_msg.joint_names)
        # print(_observation_msg)
        # """This callback is set on the subscriber node in self.__init__().
        # It's called by ROS every 40 ms while the subscriber is listening.
        # Primarily updates the present and latest times.
        # This callback is invoked asynchronously, so is effectively a
        # "subscriber thread", separate from the control flow of the rest of
        # GPS, which runs in the "main thread".
        # message: observation from the robot to store each listen."""
        # with self._time_lock:
        #     self._observations_stale[robot_id] = False
        #     self._observation_msg = message
        #     if self._currently_resetting[robot_id]:
        #         epsilon = 1e-3
        #         reset_action = self.reset_joint_angles[robot_id]
        #         now_action = np.asarray(
        #             self._observation_msg.actual.positions[:len(reset_action)])
        #         du = np.linalg.norm(reset_action-now_action, float('inf'))
        #         if du < epsilon:
        #             self._currently_resetting[robot_id] = False
                    # self._reset_cv.notify_all()
        # print('robot call back ', robot_id)
    #
    #
    #
    # def sample(self, policy, condition, sample_idx=0, verbose=True, save=True, noisy=True, test=False):
    #     """This is the main method run when the Agent object is called by GPS.
    #     Draws a sample from the environment, using the specified policy and
    #     under the specified condition.
    #     If "save" is True, then append the sample object of type Sample to
    #     self._samples[condition].
    #     TensorFlow is not yet implemented (FIXME)."""
    #     if not test:
    #         if self.parallel_on_conditions:
    #             robot_id = [condition in cond for cond in self._conds].index(True)
    #         else:
    #             robot_id = [sample_idx in sap for sap in self._samples_idx].index(True)
    #     else:
    #         robot_id = [condition in cond for cond in self._conds].index(True)
    #     with open(self.distance_files[robot_id], 'a') as f:
    #         f.write('\n\n===========================')
    #         if test:
    #             f.write("\n     Testing")
    #         f.write('\nCondition {0:d} Sample {1:d}'.format(condition, sample_idx))
    #     # Reset the arm to initial configuration at start of each new trial.
    #     self.reset(condition, robot_id=robot_id)
    #     time.sleep(3)
    #     # Generate noise to be used in the policy object to compute next state.
    #     # if noisy:
    #     #     noise = generate_noise(self.T, self.dU, self._hyperparams)
    #     # else:
    #     #     noise = np.zeros((self.T, self.dU))
    #
    #     # Execute the trial.
    #     sample_data = self._run_trial(policy, noise,
    #                                   condition=condition,
    #                                   time_to_run=self._hyperparams['trial_timeout'],
    #                                   test=test,
    #                                   robot_id=robot_id)
    #
    #     # Write trial data into sample object.
    #     sample = Sample(self)
    #     for sensor_id, data in sample_data.items():
    #         sample.set(sensor_id, np.asarray(data))
    #     sample.set(NOISE, noise)
    #
    #     # sample.set(IMAGE_FEAT, np.zeros((self._hyperparams['sensor_dims'][IMAGE_FEAT],)), t=0)
    #
    #     # kk= a
    #     # Save the sample to the data structure. This is controlled by gps_main.py.
    #     self._time_lock.acquire(True)
    #     if save:
    #         self._samples[condition].append(sample)
    #     self._time_lock.release()
    #
    #
    #     if self.condition_demo[condition] and \
    #                     self.condition_run_trial_times[condition] < self._hyperparams['num_samples']:
    #         self._time_lock.acquire(True)
    #         self.condition_run_trial_times[condition] += 1
    #         if self.condition_run_trial_times[condition] == self._hyperparams['num_samples']:
    #             self.condition_demo[condition] = False
    #         self._time_lock.release()
    #
    #     if not self.condition_demo[condition] and self.controller_demo[condition]:
    #         if not self._hyperparams.get('no_controller_learning', False):
    #             self._time_lock.acquire(True)
    #             sample_list = self.get_samples(condition, -self._hyperparams['num_samples'])
    #             X = sample_list.get_X()
    #             U = sample_list.get_U()
    #             self._time_lock.release()
    #             self.controller_prior_gmm[condition].update(X, U)
    #             policy.K, policy.k, policy.pol_covar, policy.chol_pol_covar, policy.inv_pol_covar\
    #                 = self.controller_prior_gmm[condition].fit(X, U)
    #             self.controller_demo[condition] = False
    #
    #     return sample

    def reset(self, condition, robot_id=0):
        """Not necessarily a helper function as it is inherited.
        Reset the agent for a particular experiment condition.
        condition: An index into hyperparams['reset_conditions']."""

        # Set the reset position as the initial position from agent hyperparams.
        self.reset_joint_angles[robot_id] = self._hyperparams['reset_conditions'][condition][JOINT_ANGLES]

        # Prepare the present positions to see how far off we are.
        now_position = np.asarray(self._observation_msg.actual.positions[:len(self.reset_joint_angles[robot_id])])

        # Raise error if robot has made contact with the ground in simulation.
        # This occurs because Gazebo sets joint angles beyond what they can possibly
        # be when the robot makes contact with the ground and "breaks."
        # if max(abs(now_position)) >= 2*np.pi:
        #     raise ROBOT_MADE_CONTACT_WITH_GAZEBO_GROUND_SO_RESTART_ROSLAUNCH

        # Wait until the arm is within epsilon of reset configuration.
        with self._time_lock:
            self._currently_resetting[robot_id] = True
            self._pub[robot_id].publish(self._get_ur_trajectory_message(self.reset_joint_angles[robot_id], robot_id=robot_id))
            # self._reset_cv.wait()

    def _run_trial(self, agent, time_to_run=5, test=False, robot_id=0):
        # Initialize the data structure to be passed to GPS.
        # result = {param: [] for param in self.x_data_types +
        #                  self.obs_data_types + self.meta_data_types +
        #                  [END_EFFECTOR_POINT_JACOBIANS, ACTION]}

        # Carry out the number of trials specified in the hyperparams.  The
        # index is only called by the policy.act method.  We use a while
        # instead of for because we do not want to iterate if we do not publish.
        print("I am in run trial function.")
        time_step = 0
        publish_frequencies = []
        start = timer()
        record_actions = [[] for i in range(6)]
        # sample_idx = self.condition_run_trial_times[condition]
        while rclpy.ok(): #time_step < agent['T']

            # print("Time step: ", time_step)
            # Only read and process ROS messages if they are fresh.
            if self._observations_stale[robot_id] is False:
                # # Acquire the lock to prevent the subscriber thread from
                # # updating times or observation messages.
                self._time_lock.acquire(True)
                obs_message = self._observation_msg

                # Make it so that subscriber's thread observation callback
                # must be called before publishing again.
                self._observations_stale[robot_id] = False
                # self._observations_stale_image[robot_id] = True
                # Release the lock after all dynamic variables have been updated.


                # Collect the end effector points and velocities in
                # cartesian coordinates for the state.
                # Collect the present joint angles and velocities from ROS for the state.
                last_observations = self._process_observations(obs_message, agent)
                if last_observations is None:
                    print("last_observations is empty")
                else:
                # # # Get Jacobians from present joint angles and KDL trees
                # # # The Jacobians consist of a 6x6 matrix getting its from from
                # # # (# joint angles) x (len[x, y, z] + len[roll, pitch, yaw])
                    ee_link_jacobians = self._get_jacobians(last_observations[:3])
                    if agent['link_names'][-1] is None:
                        print("End link is empty!!")
                    else:
                        # print(agent['link_names'][-1])
                        trans, rot = forward_kinematics(self.ur_chain,
                                                    agent['link_names'],
                                                    last_observations[:3],
                                                    base_link=agent['link_names'][0],
                                                    end_link=agent['link_names'][-1])
                        # #
                        rotation_matrix = np.eye(4)
                        rotation_matrix[:3, :3] = rot
                        rotation_matrix[:3, 3] = trans
                        # angle, dir, _ = rotation_from_matrix(rotation_matrix)
                        # #
                        # current_quaternion = np.array([angle]+dir.tolist())#

                        # I need this calculations for the new reward function, need to send them back to the run scara or calculate them here
                        current_quaternion = quaternion_from_matrix(rotation_matrix)

                        current_ee_tgt = np.ndarray.flatten(get_ee_points(agent['end_effector_points'],
                                                                          trans,
                                                                          rot).T)
                        ee_points = current_ee_tgt - agent['ee_points_tgt']

                        ee_points_jac_trans, _ = self._get_ee_points_jacobians(ee_link_jacobians,
                                                                               agent['end_effector_points'],
                                                                               rot)
                        ee_velocities = self._get_ee_points_velocities(ee_link_jacobians,
                                                                       agent['end_effector_points'],
                                                                       rot,
                                                                       last_observations[3:])

                        print(ee_points)

                        #
                        # Concatenate the information that defines the robot state
                        # vector, typically denoted asrobot_id 'x'.
                        state = np.r_[np.reshape(last_observations, -1),
                                      np.reshape(ee_points, -1),
                                      np.reshape(ee_velocities, -1),]

                        obs = np.r_[np.reshape(last_observations, -1),
                                      np.reshape(ee_points, -1),
                                      np.reshape(ee_velocities, -1),]
                #
                # # Stop the robot from moving past last position when sample is done.
                # # If this is not called, the robot will complete its last action even
                # # though it is no longer supposed to be exploring.
                # if time_step == agent['T']-1:
                #     action = last_observations[:6]
                # else:
                #     # add here the new policy optimization stuff
                #     action = policy.act(state, obs, time_step, noise[time_step])
                # # Primary print statements of the action development.
                # if self.parallel_num == 1:
                #     print('\nTimestep', time_step)
                # print ('Joint States ', np.around(state[:6], 2))
                # print ('Policy Action', np.around(action, 2))

                # if test:
                #     euc_distance = np.linalg.norm(ee_points.reshape(-1, 3), axis=1)
                #     if self.parallel_num == 1:
                #         for idx in range(euc_distance.shape[0]):
                #             print('   EE-Point {:d}:'.format(idx))
                #             print('   Goal: ',  np.around(self._hyperparams['ee_points_tgt'][condition, 3 * idx: 3 * idx + 3], 4))
                #             print('   Current Position: ', np.around(current_ee_tgt[3 * idx: 3 * idx + 3], 4))
                #             print('   Manhattan Distance: ', np.around(ee_points.reshape(-1, 3)[idx], 4))
                #             print('   Euclidean Distance is ', np.around(euc_distance[idx], 4))
                #     elif time_step == self._hyperparams['T'] - 1:
                #         with open(self.distance_files[robot_id], 'a') as f:
                #             for idx in range(euc_distance.shape[0]):
                #                 f.write('\n   EE-Point {:d}:'.format(idx))
                #                 f.write('\n      Goal: {:s}'.format(str(np.around(
                #                     self._hyperparams['ee_points_tgt'][condition, 3 * idx: 3 * idx + 3], 4))))
                #                 f.write('\n      Current Position: {:s}'.format(str(np.around(current_ee_tgt[3 * idx: 3 * idx + 3], 4))))
                #                 f.write('\n      Manhattan Distance: {:s}'.format(str(np.around(ee_points.reshape(-1, 3)[idx], 4))))
                #                 f.write('\n      Euclidean Distance is {:s}'.format(str(np.around(euc_distance[idx], 4))))
                #     for idx in range(6):
                #         record_actions[idx].append(action[idx])
                #     if time_step == self._hyperparams['T'] - 1:
                #         test_pair = StartEndPoints(start=tuple(self._hyperparams['reset_conditions'][condition][JOINT_ANGLES]),
                #                                    target=tuple(self._hyperparams['ee_points_tgt'][condition, :]))
                #         self.test_points_record[test_pair] = euc_distance.mean()
                # else:
                #     if self.condition_demo[condition]:
                #         demo_ee_points = (current_ee_tgt[:3] - self.demo_tgt_pos[condition][sample_idx]).reshape(-1, 3)
                #         euc_distance = np.linalg.norm(demo_ee_points, axis=1)
                #         demo_quaternion = current_quaternion - self.demo_tgt_quaternion[condition][sample_idx]
                #         if self.parallel_num == 1:
                #             print('    Demo: Euclidean Distance to Goal is {0:s}'.format(np.around(euc_distance, 4)))
                #             print('    Demo: Difference of quaternion is {0:s}'.format(np.around(demo_quaternion, 4)))
                #             print('    Demo: Target quaternion is {0:s}'.format(np.around(self.demo_tgt_quaternion[condition][sample_idx], 4)))
                #         elif time_step == self._hyperparams['T'] - 1:
                #             with open(self.distance_files[robot_id], 'a') as f:
                #                 f.write('\n    Demo: Euclidean Distance to Goal is {0:s}'.format(np.around(euc_distance, 4)))
                #                 f.write('\n    Demo: Difference of quaternion is {0:s}'.format(np.around(demo_quaternion, 4)))
                #                 f.write('\n    Demo: Target quaternion is {0:s}'.format(
                #                     np.around(self.demo_tgt_quaternion[condition][sample_idx], 4)))
                #     else:
                #         euc_distance = np.linalg.norm(ee_points.reshape(-1, 3), axis=1)
                #         if self.parallel_num == 1:
                #             for idx in range(euc_distance.shape[0]):
                #                 print('\nRobot {0:d} Euclidean Distance to Goal for EE-Point {1:d} is {2:f}'.format(robot_id, idx, np.around(euc_distance[idx], 4)))
                #                 print('\nRobot {0:d} Euclidean Distance to Goal {1:d} for (x,y,z)) is: '.format(robot_id, idx), np.around(ee_points,4))
                #                 #save distances to file
                #             with open(self.distance_files[robot_id], 'a') as f:
                #                 for idx in range(euc_distance.shape[0]):
                #                     for idx in range(euc_distance.shape[0]):
                #                         # here we save each itteration
                #                         f.write('\nRobot {0:d} Euclidean Distance to Goal for EE-Point {1:d} is {2:f}'.format(robot_id, idx, np.around(euc_distance[idx], 4)))
                #                         f.write('\nRobot {0:d} Euclidean Distance to Goal {1:d} for: \n x: {4:f}, y: {4:f}, z: {4:f} '.format(robot_id, idx, np.around(ee_points[0],4),np.around(ee_points[1],4),np.around(ee_points[2],4)))
                #         elif time_step == self._hyperparams['T'] - 1:
                #             with open(self.distance_files[robot_id], 'a') as f:
                #                 for idx in range(euc_distance.shape[0]):
                #                     # here we can save last itteration
                #                     f.write('\nRobot {0:d} Euclidean Distance to Goal for EE-Point {1:d} is {2:f}'.format(robot_id, idx, np.around(euc_distance[idx], 4)))
                #                     f.write('\nRobot {0:d} Euclidean Distance to Goal {1:f} for: \n x: {4:d}, y: {4:f}, z: {4:f} '.format(robot_id, idx, np.around(ee_points[0],4),np.around(ee_points[1],4),np.around(ee_points[2],4)))

                # Publish the action to the robot.
                # self._pub.publish(self._get_ur_trajectory_message(action,
                                                                            # self._hyperparams['slowness'],
                                                                            # robot_id=robot_id))


                # print("Updating time step")
        #
        #         # Build up the result data structure to return to GPS.
        #         result[ACTION].append(action)
        #         # result[END_EFFECTOR_ROTATIONS].append(quaternion)
        #         result[END_EFFECTOR_POINTS].append(ee_points)
        #         result[END_EFFECTOR_POINT_JACOBIANS].append(ee_points_jac_trans)
        #         result[END_EFFECTOR_POINT_VELOCITIES].append(ee_velocities)
        #
        #         if time_step > 1:
        #             end = timer()
        #             elapsed_time = end-start
        #             frequency = 1 / float(elapsed_time)
        #             if self.parallel_num == 1:
        #                 print('Time interval(s): {0:8.4f},  Hz: {1:8.4f}'.format(elapsed_time, frequency))
        #             publish_frequencies.append(frequency)
        #         start = timer()
        #     # The subscriber is listening during this sleep() call, and
            # updating the time "continuously" (each hyperparams['period'].
            # self.r[robot_id].sleep()
        #
        #
        # self.print_process(publish_frequencies, record_actions, condition, test=test, robot_id=robot_id)
        # # Sanity check the results to make sure nothing is infinite.
        # for value in result.values():
        #     if not np.isfinite(value).all():
        #         print('There is an infinite value in the results.')
        #     assert np.isfinite(value).all()
        # return result
                self._time_lock.release()

                rclpy.spin_once(node)
                # sleep(0.05)
                # node.destroy_node()
                # rclpy.shutdown()
                # Only update the time_step after publishing.
                time_step += 1
    #
    #
    # def print_process(self, publish_frequencies, record_actions, condition, test=False, robot_id=0):
    #     n, min_max, mean, var, skew, kurt = stats.describe(publish_frequencies)
    #     median = np.median(publish_frequencies)
    #     first_quantile = np.percentile(publish_frequencies, 25)
    #     third_quantile = np.percentile(publish_frequencies, 75)
    #     print('\nRobot ' + str(robot_id) +' Publisher frequencies statistics:')
    #     print('Robot ' + str(robot_id) +' Minimum: {0:9.4f} Maximum: {1:9.4f}'.format(min_max[0], min_max[1]))
    #     print('Robot ' + str(robot_id) +' Mean: {0:9.4f}'.format(mean))
    #     print('Robot ' + str(robot_id) +' Variance: {0:9.4f}'.format(var))
    #     print('Robot ' + str(robot_id) +' Median: {0:9.4f}'.format(median))
    #     print('Robot ' + str(robot_id) +' First quantile: {0:9.4f}'.format(first_quantile))
    #     print('Robot ' + str(robot_id) +' Third quantile: {0:9.4f}'.format(third_quantile))
    #     if test:
    #         # fig, axes = plt.subplots(2, 3)
    #         # for idx in range(6):
    #         #     axes[idx / 3, idx % 3].plot(record_actions[idx])
    #         #     axes[idx / 3, idx % 3].set_title(self._hyperparams['joint_order'][robot_id][idx])
    #         # figname = self._hyperparams['control_plot_dir'] + str('{:04d}'.format(condition)) + '.png'
    #         # plt.savefig(figname, bbox_inches='tight')
    #         print '\n============================='
    #         print '============================='
    #         print('Condition {:d} Testing finished'.format(condition))
    #         print '============================='
    #         print '============================='
    #
    #         if condition == self._hyperparams['ee_points_tgt'].shape[0] - 1:
    #             print '\n============================='
    #             print '============================='
    #             print('    All Testings finished    ')
    #             print '============================='
    #             print '============================='
    #             np.set_printoptions(precision=4, suppress=True)
    #             distances = np.array(self.test_points_record.values())
    #             threshold = 0.01
    #             percentage = (distances <= threshold).sum() / float(distances.size) * 100.0
    #             percentage_double_thr = (distances <= 2 * threshold).sum() / float(distances.size) * 100.0
    #             for key, value in self.test_points_record.items():
    #                 starting_point = np.array(key.start)
    #                 target_point = np.array(key.target).reshape(-1, 3)
    #                 distance = value
    #                 print '  Starting joint angles: ', starting_point
    #                 for idx in range(target_point.shape[0]):
    #                     print '      Target point: ', target_point[idx]
    #                 print('    Average distance: {:6.4f}'.format(distance))
    #
    #             print("\nConditions with final distance greater than {0:.3f}m:".format(threshold))
    #             for key, value in self.test_points_record.items():
    #                 starting_point = np.array(key.start)
    #                 target_point = np.array(key.target).reshape(-1, 3)
    #                 distance = value
    #                 if distance > threshold:
    #                     print '  Starting joint angles: ', starting_point
    #                     for idx in range(target_point.shape[0]):
    #                         print '      Target point: ', target_point[idx]
    #                     print('    Average distance: {:6.4f}'.format(distance))
    #
    #             n, min_max, mean, var, skew, kurt = stats.describe(distances)
    #             median = np.median(distances)
    #             first_quantile = np.percentile(distances, 25)
    #             third_quantile = np.percentile(distances, 75)
    #             print('\nDistances statistics:')
    #             print("Minimum: {0:9.4f} Maximum: {1:9.4f}".format(min_max[0], min_max[1]))
    #             print("Mean: {0:9.4f}".format(mean))
    #             print("Variance: {0:9.4f}".format(var))
    #             print("Median: {0:9.4f}".format(median))
    #             print("First quantile: {0:9.4f}".format(first_quantile))
    #             print("Third quantile: {0:9.4f}".format(third_quantile))
    #             print("Percentage of conditions with final distance less than {0:.3f}m is: {1:4.2f} %".format(threshold, percentage))
    #             print("Percentage of conditions with final distance less than {0:.3f}m is: {1:4.2f} %".format(2 * threshold, percentage_double_thr))
    #
    #
    #
    def _get_jacobians(self, state, robot_id=0):
        """Produce a Jacobian from the urdf that maps from joint angles to x, y, z.
        This makes a 6x6 matrix from 6 joint angles to x, y, z and 3 angles.
        The angles are roll, pitch, and yaw (not Euler angles) and are not needed.
        Returns a repackaged Jacobian that is 3x6.
        """

        # Initialize a Jacobian for 6 joint angles by 3 cartesian coords and 3 orientation angles
        jacobian = Jacobian(3)

        # Initialize a joint array for the present 6 joint angles.
        angles = JntArray(3)

        # Construct the joint array from the most recent joint angles.
        for i in range(3):
            angles[i] = state[i]

        # Update the jacobian by solving for the given angles.
        self.jac_solver.JntToJac(angles, jacobian)

        # Initialize a numpy array to store the Jacobian.
        J = np.array([[jacobian[i, j] for j in range(jacobian.columns())] for i in range(jacobian.rows())])

        # Only want the cartesian position, not Roll, Pitch, Yaw (RPY) Angles
        ee_jacobians = J
        return ee_jacobians


    def _process_observations(self, message, agent, robot_id=0):
        """Helper fuinction only called by _run_trial to convert a ROS message
        to joint angles and velocities.
        Check for and handle the case where a message is either malformed
        or contains joint values in an order different from that expected
        in hyperparams['joint_order']"""
        # print(message)
        # len(message)
        if not message:
            print("Message is empty");
        else:
            # # Check if joint values are in the expected order and size.
            if message.joint_names != agent['joint_order']:
                # Check that the message is of same size as the expected message.
                if len(message.joint_names) != len(agent['joint_order']):
                    raise MSG_INVALID_JOINT_NAMES_DIFFER

                # Check that all the expected joint values are present in a message.
                if not all(map(lambda x,y: x in y, message.joint_names,
                    [self._valid_joint_set[robot_id] for _ in range(len(message.joint_names))])):
                    raise MSG_INVALID_JOINT_NAMES_DIFFER
                    print("Joints differ")

            return np.array(message.actual.positions + message.actual.velocities)

                # # If necessary, reorder the joint values to conform to the order
                # # expected in hyperparams['joint_order'].
                # new_message = [None for _ in range(len(message))]
                # print(new_message)
                # for joint, index in message.joint_names.enumerate():
                #     for state_type in self._hyperparams['state_types']:
                #         new_message[self._valid_joint_index[robot_id][joint]] = message[state_type][index]
                #
                # message = new_message
                # #
                # # # Package the positions, velocities, amd accellerations of the joint angles.
                # # for (state_type, state_category), state_value_vector in zip(
                # #     # self.agent['state_types'].items(),
                # #     [message.actual.positions, message.actual.velocities,
                # #     message.actual.accelerations]):
                # #
                # #     # Assert that the length of the value vector matches the corresponding
                # #     # number of dimensions from the hyperparameters file
                # #     # assert len(state_value_vector) == self._hyperparams['sensor_dims'][state_category]
                # #
                # #     # Write the state value vector into the results dictionary keyed by its
                # #     # state category
                # #     result[state_category].append(state_value_vector)
                # #     print(result)
                # #


    def _get_ur_trajectory_message(self, action, robot_id=0):
        """Helper function only called by reset() and run_trial().
        Wraps an action vector of joint angles into a JointTrajectory message.
        The velocities, accelerations, and effort do not control the arm motion"""

        # Set up a trajectory message to publish.
        action_msg = JointTrajectory()
        action_msg.joint_names = self._hyperparams['joint_order'][robot_id]

        # Create a point to tell the robot to move to.
        target = JointTrajectoryPoint()
        target.positions = action

        # These times determine the speed at which the robot moves:
        # it tries to reach the specified target position in 'slowness' time.
        target.time_from_start = rospy.Duration(slowness)

        # Package the single point into a trajectory of points with length 1.
        action_msg.points = [target]

        return action_msg

    def _get_ee_points_jacobians(self, ref_jacobian, ee_points, ref_rot):
        """
        Get the jacobians of the points on a link given the jacobian for that link's origin
        :param ref_jacobian: 6 x 6 numpy array, jacobian for the link's origin
        :param ee_points: N x 3 numpy array, points' coordinates on the link's coordinate system
        :param ref_rot: 3 x 3 numpy array, rotational matrix for the link's coordinate system
        :return: 3N x 6 Jac_trans, each 3 x 6 numpy array is the Jacobian[:3, :] for that point
                 3N x 6 Jac_rot, each 3 x 6 numpy array is the Jacobian[3:, :] for that point
        """
        ee_points = np.asarray(ee_points)
        ref_jacobians_trans = ref_jacobian[:3, :]
        ref_jacobians_rot = ref_jacobian[3:, :]
        end_effector_points_rot = np.expand_dims(ref_rot.dot(ee_points.T).T, axis=1)
        ee_points_jac_trans = np.tile(ref_jacobians_trans, (ee_points.shape[0], 1)) + \
                                        np.cross(ref_jacobians_rot.T, end_effector_points_rot).transpose(
                                            (0, 2, 1)).reshape(-1, 3)
        ee_points_jac_rot = np.tile(ref_jacobians_rot, (ee_points.shape[0], 1))
        return ee_points_jac_trans, ee_points_jac_rot

    def _get_ee_points_velocities(self, ref_jacobian, ee_points, ref_rot, joint_velocities):
        """
        Get the velocities of the points on a link
        :param ref_jacobian: 6 x 6 numpy array, jacobian for the link's origin
        :param ee_points: N x 3 numpy array, points' coordinates on the link's coordinate system
        :param ref_rot: 3 x 3 numpy array, rotational matrix for the link's coordinate system
        :param joint_velocities: 1 x 6 numpy array, joint velocities
        :return: 3N numpy array, velocities of each point
        """
        ref_jacobians_trans = ref_jacobian[:3, :]
        ref_jacobians_rot = ref_jacobian[3:, :]
        ee_velocities_trans = np.dot(ref_jacobians_trans, joint_velocities)
        ee_velocities_rot = np.dot(ref_jacobians_rot, joint_velocities)
        ee_velocities = ee_velocities_trans + np.cross(ee_velocities_rot.reshape(1, 3),
                                                       ref_rot.dot(ee_points.T).T)
        return ee_velocities.reshape(-1)
