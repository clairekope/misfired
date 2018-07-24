import pickle
import requests
import h5py
import sys
import os
import numpy as np
import astropy.units as u
import matplotlib; matplotlib.use('agg')
import matplotlib.pyplot as plt
from astropy.cosmology import WMAP9
from mpi4py import MPI
import pdb

comm = MPI.COMM_WORLD
rank = comm.Get_rank()
size = comm.Get_size()

def scatter_work(array, mpi_rank, mpi_size, root=0):
    if mpi_rank == root:
        scatter_total = array.size
        mod = scatter_total % mpi_size
        if mod != 0:
            print("Padding array for scattering...")
            pad = -1 * np.ones(mpi_size - mod, dtype='i')
            array = np.concatenate((array, pad))
            scatter_total += mpi_size - mod
            assert scatter_total % mpi_size == 0
            assert scatter_total == array.size
    else:
        scatter_total = None
        #array = None

    scatter_total = comm.bcast(scatter_total, root=root)
    subset = np.empty(scatter_total//mpi_size, dtype='i')
    comm.Scatter(array, subset, root=root)

    return subset

def get(path, params=None):
    # make HTTP GET request to path
    headers = {"api-key":"5309619565f744f9248320a886c59bec"}
    r = requests.get(path, params=params, headers=headers)

    # raise exception if response code is not HTTP SUCCESS (200)
    r.raise_for_status()

    if r.headers['content-type'] == 'application/json':
        return r.json() # parse json responses automatically

    if 'content-disposition' in r.headers:
        filename = "gas_cutouts/" + r.headers['content-disposition'].split("filename=")[1]
        with open(filename, 'wb') as f:
            f.write(r.content)
        return filename # return the filename string

    return r

def periodic_centering(x, center, boxsixe):
    # stack two periodic boxes next to each other
    xx = np.concatenate((x, boxsize+x))
    # if the center is on the left side of the box,
    # move it over one boxlength to put it near the center of xx
    if center < boxsize/2:
        center +=  boxsize
    crit = np.logical_and(xx >= center-boxsize/2,
                          xx <= center+boxsize/2)
    #try:
    assert x.size == xx[crit].size
    #except AssertionError:
    #    pdb.set_trace()
    return xx[crit] - center

# MAIN

if rank==0:
    with open("cut4.pkl","rb") as f:
        subs = pickle.load(f)
    sub_list = np.array([k for k in subs.keys()])
else:
    subs = {}
    sub_list = None
subs = comm.bcast(subs,root=0)
my_subs = scatter_work(sub_list, rank, size)
my_cut_inst_ssfr = {}
my_all_inst_ssfr = {}

url = "http://www.illustris-project.org/api/Illustris-1/snapshots/135/subhalos/"
cutout = {"gas":
        "Coordinates,Density,Masses,NeutralHydrogenAbundance,StarFormationRate,InternalEnergy"}

boxsize = get("http://www.illustris-project.org/api/Illustris-1")['boxsize']

good_ids = np.where(my_subs > -1)[0]

for sub_id in my_subs[good_ids]:
    file = "gas_cutouts/cutout_{}.hdf5".format(sub_id)
    if not os.path.isfile(file):
        print("Rank", rank, "downloading",sub_id); sys.stdout.flush()
        get(url + str(sub_id) + "/cutout.hdf5", cutout)
    sub = get(url+str(sub_id))

    try:
        with h5py.File(file) as f:
            coords = f['PartType0']['Coordinates'][:,:]
            #mass = f['PartType0']['Masses'][:]
            #dens = f['PartType0']['Density'][:]
            #inte = f['PartType0']['InternalEnergy'][:]
            #HI = f['PartType0']['NeutralHydrogenAbundance'][:]
            sfr = f['PartType0']['StarFormationRate'][:]
    except KeyError:
        print(sub_id); sys.stdout.flush()
        continue
    with h5py.File("stellar_cutouts/cutout_{}.hdf5".format(sub_id)) as f:
        scoords = f['PartType4']['Coordinates'][:]
        smass = f['PartType4']['Masses'][:]
        a = f['PartType4']['GFM_StellarFormationTime']

    x = coords[:,0]
    y = coords[:,1]
    z = coords[:,2]
    x_rel = periodic_centering(x, sub['pos_x'], boxsize) * u.kpc / 0.704
    y_rel = periodic_centering(y, sub['pos_y'], boxsize) * u.kpc / 0.704
    z_rel = periodic_centering(z, sub['pos_z'], boxsize) * u.kpc / 0.704
    r = np.sqrt(x_rel**2 + y_rel**2 + z_rel**2)    
    tot_sfr = np.sum(sfr[r < 2*u.kpc]) * u.Msun/u.yr 
    
    my_all_inst_ssfr[sub_id] = {}
    my_all_inst_ssfr[sub_id]['SFR'] = tot_sfr

    sx = scoords[:,0]
    sy = scoords[:,1]
    sz = scoords[:,2]
    sx_rel = periodic_centering(sx, sub['pos_x'], boxsize) * u.kpc / 0.704
    sy_rel = periodic_centering(sy, sub['pos_x'], boxsize) * u.kpc / 0.704
    sz_rel = periodic_centering(sz, sub['pos_x'], boxsize) * u.kpc / 0.704
    sr = np.sqrt(sx_rel**2 + sy_rel**2 + sz_rel**2)    
    smass = smass * 1e10 / 0.704 * u.Msun

    ssfr = tot_sfr / np.sum(smass[sr < 2*u.kpc]) 
    
    my_all_inst_ssfr[sub_id]['sSFR'] = ssfr
    if ssfr > 1e-11/u.yr:
        my_cut_inst_ssfr[sub_id] = subs[sub_id]
        my_cut_inst_ssfr[sub_id]['inst_sSFR'] = ssfr

cut_ssfr_lst = comm.gather(my_cut_inst_ssfr, root=0)
all_ssfr_lst = comm.gather(my_all_inst_ssfr, root=0)
if rank==0:
    cut_ssfr = {}
    for dic in cut_ssfr_lst:
        for k,v in dic.items():
            cut_ssfr[k] = v
    with open("cut_inst_ssfr.pkl","wb") as f:
        pickle.dump(cut_ssfr, f)
    
    all_ssfr = {}
    for dic in all_ssfr_lst:
        for k,v in dic.items():
            all_ssfr[k] = v
    with open("cut4_all_inst_ssfr.pkl","wb") as f:
        pickle.dump(all_ssfr,f)