# create an interface similar to pytorch3d's knn
from collections import namedtuple
from typing import Union

import torch

from frnn import _C
import torch
from torch.autograd import Function
from torch.autograd.function import once_differentiable

_GRID = namedtuple("GRID", "sorted_points grid_off sorted_points_idxs, grid_params")

# TODO: implement backward pass for frnn and implement this operation as an autograd.Function

GRID_PARAMS_SIZE = 8
MAX_RES = 100

def frnn_grid_points(
  points1: torch.Tensor,
  points2: torch.Tensor,
  lengths1: Union[torch.Tensor, None] = None,
  lengths2: Union[torch.Tensor, None] = None,
  grid: Union[_GRID, None] = None,
  # sorted_points2: Union[torch.Tensor, None] = None,
  # sorted_points2_idxs: Union[torch.Tensor, None] = None,
  # grid_off: Union[torch.Tensor, None] = None,
  K: int = -1,
  r: float = -1,
  return_nn: bool = False,
  return_sorted: bool = True,     # for now we always sort the neighbors by dist
  return_grid: bool = False,      # for reusing grid structure
):
  """
  TODO: add docs here
  """

  if points1.shape[0] != points2.shape[0]:
    raise ValueError("points1 and points2 must have the same batch  dimension")
  if points1.shape[2] != 3 or points2.shape[2] != 3:
    raise ValueError("for now only grid in 3D is supported")
  if not points1.is_cuda or not points2.is_cuda:
    raise TypeError("for now only cuda version is supported")

  points1 = points1.contiguous()
  points2 = points2.contiguous()

  P1 = points1.shape[1]
  P2 = points1.shape[1]

  if lengths1 is None:
    lengths1 = torch.full((points1.shape[0],), P1, dtype=torch.long, device=points1.device)
  if lengths2 is None:
    lengths2 = torch.full((points2.shape[0],), P2, dtype=torch.long, device=points2.device)

  if grid is None:
    # setup grid params
    N = points1.shape[0]
    grid_params_cuda = torch.zeros((N, GRID_PARAMS_SIZE), dtype=torch.float, device=points1.device)

    G = -1
    for i in range(N):
      # 0-2 grid_min; 3 grid_delta; 4-6 grid_res; 7 grid_total
      grid_min = points2[i, :lengths2[i]].min(dim=0)[0]
      grid_max = points2[i, :lengths2[i]].max(dim=0)[0]
      grid_params_cuda[i, :3] = grid_min
      grid_size = grid_max - grid_min
      cell_size = r
      if cell_size < grid_size.min()/MAX_RES:
        cell_size = grid_size.min() / MAX_RES
      grid_params_cuda[i, 3] = 1 / cell_size
      grid_params_cuda[i, 4:7] = torch.floor(grid_size / cell_size) + 1
      grid_params_cuda[i, 7] = grid_params_cuda[i, 4] * grid_params_cuda[i, 5] * grid_params_cuda[i, 6] 
      if G < grid_params_cuda[i, 7]:
        G = int(grid_params_cuda[i, 7].item())

    # test setup_grid_params  
    # print("Grid Params:\n", grid_params_cuda)

    grid_cnt = torch.zeros((N, G), dtype=torch.int, device=points1.device)
    grid_cell = torch.full((N, P2), -1, dtype=torch.int, device=points1.device)
    grid_idx = torch.full((N, P2), -1, dtype=torch.int, device=points1.device)

    _C.insert_points_cuda(points2, lengths2, grid_params_cuda, grid_cnt, grid_cell, grid_idx, G)

    # test insert_points
    # return grid_cnt, grid_cell, grid_idx

    grid_off = _C.prefix_sum_cuda(grid_cnt, grid_params_cuda.cpu())
    # test_prefix_sum
    # return grid_off
    # test_counting_sort (need to output grid_idx for comparison)
    # return grid_off, grid_cnt, grid_cell, grid_idx

    sorted_points2 = torch.zeros((N, P2, 3), dtype=torch.float, device=points1.device)
    sorted_points2_idxs = torch.full((N, P2), -1, dtype=torch.int, device=points1.device)

    _C.counting_sort_cuda(
      points2,
      lengths2,
      grid_cell,
      grid_idx,
      grid_off,
      sorted_points2,
      sorted_points2_idxs
    )

    idxs, dists = _C.find_nbrs_cuda(
      points1,
      sorted_points2,
      lengths1,
      lengths2,
      grid_off,
      sorted_points2_idxs,
      grid_params_cuda,
      K,
      r
    )
  else:
    idxs, dists = _C.find_nbrs_cuda(
      points1,
      grid[0],        # sorted_points2
      lengths1,
      lengths2,
      grid[1],        # grid_off
      grid[2],        # sorted_points2_idxs
      grid[3],        # grid_params_cuda
      K,
      r
    )
    # use cached grid
    
  # for now we don't gather here
  nn = None

  if return_grid:
    if grid is not None:
      return idxs, dists, nn, grid
    else:
      return idxs, dists, nn, \
        _GRID(
          sorted_points=sorted_points2, 
          grid_off=grid_off, 
          sorted_points_idxs=sorted_points2_idxs, 
          grid_params=grid_params_cuda)
  else:
    return idxs, dists, nn, None