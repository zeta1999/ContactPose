"""
ContactPose dataset loading utilities
"""
import os
import json
import numpy as np
import pickle

from . import misc as mutils

osp = os.path


def get_object_names(p_num, intent):
  """
  returns list of objects grasped in this session
  """
  sess_dir = 'full{:d}_{:s}'.format(p_num, intent)
  sess_dir = osp.join('data', 'contactpose_data', sess_dir)
  return list(next(os.walk(sess_dir))[1])


def get_intents(p_num, object_name):
  """
  returns list of intents with which this participant grasped object
  """
  out = []
  for ins in ('use', 'handoff'):
    sess_dir = 'full{:d}_{:s}'.format(p_num, intent)
    sess_dir = osp.join('data', 'contactpose_data', sess_dir, object_name)
    if osp.isdir(sess_dir):
      out.append(ins)
  return out

    
def get_p_nums(object_name, intent):
  """
  returns list of participants who grasped this object with this intent
  """
  out = []
  for p_num in range(1, 51):
    sess_dir = 'full{:d}_{:s}'.format(p_num, intent)
    sess_dir = osp.join('data', 'contactpose_data', sess_dir, object_name)
    if osp.isdir(sess_dir):
      out.append(p_num)
  return out


class ContactPose(object):
  """
  Base class for accessing the ContactPose dataset
  """
  _mano_dicts = None  # class variable so that large data is not loaded repeatedly
  def __init__(self, p_num, intent, object_name, mano_pose_params=15):
    if (object_name == 'palm_print') or (object_name == 'hands'):
      print('This class is not meant to be used with palm_print or hands')
      return
    self.p_num = p_num
    self.intent = intent
    self.object_name = object_name
    self._mano_pose_params = mano_pose_params
  
    p_id = 'full{:d}_{:s}'.format(p_num, intent)
    self.data_dir = osp.join('data', 'contactpose_data', p_id, object_name)
    assert(osp.isdir(self.data_dir))
    
    # read grasp data
    with open(self.annotation_filename, 'r') as f:
      ann = json.load(f)
    self._n_frames = len(ann['frames'])
    self._valid_cameras = [cn for cn,cv in ann['cameras'].items() if cv['valid']]
    self._is_object_pose_optimized = [f['object_pose_optimized'] for
                                      f in ann['frames']]
    self._valid_hands = [hand_idx for hand_idx, hand in enumerate(ann['hands'])
                         if hand['valid']]

    im_filenames = {}
    for camera_name in self.valid_cameras:
      im_dir = osp.join(self.data_dir, 'images_full', camera_name, '{:s}')
      im_filenames[camera_name] = [
          osp.join(im_dir, 'frame{:03d}.png'.format(i)) for i in range(len(self))]
    self._im_filenames = [{k: v for k,v in zip(im_filenames.keys(), vv)} for
                         vv in zip(*im_filenames.values())]

    oX = []   # 3D joints w.r.t. object
    all_oTh = []
    for hand_idx, hand in enumerate(ann['hands']):
      if hand['valid']:
        hX = np.asarray(hand['joints'])  # hand joints w.r.t. hand root
        if hand['moving']:
          # object pose w.r.t. hand
          oThs = [np.linalg.inv(mutils.pose_matrix(f['hTo'][hand_idx])) for f
                  in ann['frames']]
          all_oTh.append(oThs)
          oX.append([mutils.tform_points(oTh, hX) for oTh in oThs])
        else:
          oX.append([hX for _ in range(len(self))])
          all_oTh.append([np.eye(4) for _ in range(len(self))])
      else:
        oX.append([None for _ in range(len(self))])
        all_oTh.append([np.eye(4) for _ in range(len(self))])
    self._oX = list(map(tuple, zip(*oX)))
    self._oTh = list(map(tuple, zip(*all_oTh)))

    # world pose w.r.t. object
    oTws = [mutils.pose_matrix(f['oTw']) for f in ann['frames']]
    self._cTo = {}  # object pose w.r.t. camera
    self._K   = {}  # camera intrinsics
    for camera_name in self.valid_cameras:
      cam = ann['cameras'][camera_name]
      K = [[cam['K']['fx'], 0, cam['K']['cx']],
           [0, cam['K']['fy'], cam['K']['cy']],
           [0, 0, 1]]
      self._K[camera_name] = \
        mutils.get_A(camera_name, cam['K']['width'], cam['K']['height']) @ K
      # camera pose w.r.t. world
      wTc = mutils.pose_matrix(cam['wTc'])
      self._cTo[camera_name] = [np.linalg.inv(oTw @ wTc) for oTw in oTws]

    # projections
    self._ox = []  # joint projections
    self._om = []  # marker projections
    # 3D marker locations w.r.t. object
    oM = np.loadtxt(osp.join('data', 'object_marker_locations',
                             '{:s}_final_marker_locations.txt'.
                             format(object_name)))[:, :3]
    for frame_idx in range(len(self)):
      this_ox = {}
      this_om = {}
      for camera_name in self.valid_cameras:
        this_om[camera_name] = \
          mutils.project(self._K[camera_name],
                         mutils.tform_points(self._cTo[camera_name][frame_idx], oM))
        x = []
        for hand_idx in range(2):
          if hand_idx not in self._valid_hands:
            x.append(None)
          else:
            x.append(mutils.project(self._K[camera_name],
                                    mutils.tform_points(self._cTo[camera_name][frame_idx],
                                                        self._oX[frame_idx][hand_idx])))
        this_ox[camera_name] = tuple(x)
      self._ox.append(this_ox)
      self._om.append(this_om)

    # load MANO data for the class
    if ContactPose._mano_dicts is not None:
      return
    ContactPose._mano_dicts = []
    for hand_name in ('LEFT', 'RIGHT'):
      filename = osp.join('thirdparty', 'mano', 'models',
                          'MANO_{:s}.pkl'.format(hand_name))
      with open(filename, 'rb') as f:
        ContactPose._mano_dicts.append(pickle.load(f, encoding='latin1'))


  def __len__(self):
    """
    Number of RGB-D time frames
    """
    return self._n_frames

  def __repr__(self):
    hand_names = ['left', 'right']
    hand_str = ' '.join([hand_names[i] for i in self._valid_hands])
    return 'Participant {:d}, intent {:s}, object {:s}\n'.format(self.p_num,
                                                                 self.intent,
                                                                 self.object_name) +\
            '{:d} frames\n'.format(len(self)) +\
            'Cameras present: {:s}\n'.format(' '.join(self.valid_cameras)) +\
            'Hands present: {:s}'.format(hand_str)

  @property
  def contactmap_filename(self):
    return osp.join(self.data_dir, '{:s}.ply'.format(self.object_name))

  @property
  def annotation_filename(self):
    return osp.join(self.data_dir, 'annotations.json')

  @property
  def mano_filename(self):
    """
    return name of file containing MANO fit params
    """
    return osp.join(self.data_dir,
                    'mano_fits_{:d}.json'.format(self._mano_pose_params))

  @property
  def valid_cameras(self):
    """
    return list of cameras valid for this grasp
    """
    return self._valid_cameras 

  @property
  def mano_params(self):
    """
    List of 2 [left, rigtht]. Each element is None or a dict containing
    'pose' (PCA pose space of dim self._mano_pose_params),
    'betas' (PCA shape space), and root transform 'hTm'
    """
    with open(self.mano_filename, 'r') as f:
      params = json.load(f)
    out = []
    for p in params:
      if not p['valid']:
        out.append(None)
        continue
    
      # MANO root pose w.r.t. hand
      hTm = np.linalg.inv(mutils.pose_matrix(p['mTc']))
      out.append({
        'pose': p['pose'],
        'betas': p['betas'],
        'hTm': hTm,
      })
    return out

  def image_filenames(self, mode, frame_idx):
    """
    return dict with full image filenames for all valid cameras
    mode = color or depth
    """
    return {k: v.format(mode) for k,v in self._im_filenames[frame_idx].items()}

  def hand_joints(self, frame_idx=None):
    """
    3D hand joints w.r.t. object
    randomly sampled time frame if frame_idx is None
    tuple of length 2, 21 joints per hand, None if hand is not present
    """
    if frame_idx is None:
      frame_idx = np.random.choice(len(self))
    return self._oX[frame_idx]

  def K(self, camera_name):
    """
    Camera intrinsics 3x3
    """
    return self._K[camera_name]

  def object_pose(self, camera_name, frame_idx):
    """
    Pose of object w.r.t. camera at frame frame_idx
    4x4 homogeneous matrix
    """
    return self._cTo[camera_name][frame_idx]

  def projected_hand_joints(self, camera_name, frame_idx):
    """
    hand joints projected into camera image
    tuple of length 2
    21x2 or None based on if hand is present in this grasp
    """
    return self._ox[frame_idx][camera_name]

  def projected_object_markers(self, camera_name, frame_idx):
    """
    object markers projected into camera image
    Nx2 where N in [5, 10]
    """
    return self._om[frame_idx][camera_name]

  def mano_meshes(self, frame_idx=None):
    """
    return list of 2 dicts. Element is None if that hand is absent,
    or contains 'vertices', 'faces', and 'joints'
    """
    if frame_idx is None:
      frame_idx = np.random.choice(len(self))
    return mutils.load_mano_meshes(self.mano_params, ContactPose._mano_dicts,
      self._oTh[frame_idx])