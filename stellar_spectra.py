
# coding: utf-8

import requests
import h5py
import fsps
import pickle
import sys
import numpy as np
import matplotlib.pyplot as plt
import astropy.units as u
from mpi4py import MPI

use_inst = True

comm = MPI.COMM_WORLD
rank = comm.Get_rank()
size = comm.Get_size()

def scatter_work(array, mpi_rank, mpi_size, root=0):
    """ array should only exist on root & be None elsewhere"""
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
        filename = r.headers['content-disposition'].split("filename=")[1]
        with open(filename, 'wb') as f:
            f.write(r.content)
        return filename # return the filename string

    return r

def periodic_centering(x, center, boxsixe):
    # stack two periodic boxes next to each other
    xx = np.concatenate((x, boxsize+x))
    if center < boxsize/2:
        center +=  boxsize
    crit = np.logical_and(xx >= center-boxsize/2,
                          xx <= center+boxsize/2)
    assert x.size == xx[crit].size
    
    return xx[crit] - center


sp = fsps.StellarPopulation(zcontinuous=1, sfh=3)

sp.params['add_agb_dust_model'] = True 
sp.params['add_dust_emission'] = False
sp.params['add_igm_absorption'] = False
sp.params['add_neb_emission'] = True
sp.params['add_neb_continuum'] = True
sp.params['add_stellar_remnants'] = False
    
sp.params['dust_type'] = 0 # Charlot & Fall type; parameters from Torrey+15
sp.params['dust_tesc'] = np.log10(3e7)
sp.params['dust1'] = 1
sp.params['dust2'] = 1.0/3.0 

sp.params['imf_type'] = 1 # Chabrier (2003)


if rank==0:
    with open("cut4.pkl","rb") as f:
        sample = pickle.load(f)
    sub_list = np.array([k for k in sample.keys()])
    if use_inst:
        with open("cut4_all_inst_ssfr.pkl","rb") as f:
            inst_sfr = pickle.load(f)
else:
    sample = {}
    sub_list = None
    if use_inst:
        inst_sfr = {}
                                   
if use_inst:
    inst_sfr = comm.bcast(inst_sfr, root=0)
my_subs = scatter_work(sub_list, rank, size)
good_ids = np.where(my_subs > -1)[0]

url = "http://www.illustris-project.org/api/Illustris-1/snapshots/135/subhalos/"
boxsize = 75000
H0 = 0.704 * 100
omegaM = 0.2726
omegaL = 0.7274
timenow = 2.0/(3.0*H0) * 1./np.sqrt(omegaL) \
          * np.log(np.sqrt(omegaL*1./omegaM) \
          + np.sqrt(omegaL*1./omegaM+1))\
          * 3.08568e19/3.15576e16 * u.Gyr

for sub_id in my_subs[good_ids]:
    if use_inst:
        if sub_id not in inst_sfr: # it doesnt have gas!
            continue   
                                   
    sub = get(url + str(sub_id))
    
    file = "stellar_cutouts/cutout_{}.hdf5".format(sub_id)
    with h5py.File(file) as f:
        coords = f['PartType4']['Coordinates'][:,:]
        a = f['PartType4']['GFM_StellarFormationTime'][:] # as scale factor
        init_mass = f['PartType4']['GFM_InitialMass'][:]
        curr_mass = f['PartType4']['Masses'][:]
        metals = f['PartType4']['GFM_Metallicity'][:]

    stars = a > 0

    x = coords[:,0][stars] # throw out wind particles (a < 0)
    y = coords[:,1][stars]
    z = coords[:,2][stars]
    x_rel = periodic_centering(x, sub['pos_x'], boxsize) * u.kpc / 0.704
    y_rel = periodic_centering(y, sub['pos_y'], boxsize) * u.kpc / 0.704
    z_rel = periodic_centering(z, sub['pos_z'], boxsize) * u.kpc / 0.704
    r = np.sqrt(x_rel**2 + y_rel**2 + z_rel**2)

    central = r < 2*u.kpc

    init_mass = init_mass[stars][central] * 1e10 #* u.Msun
    curr_mass = curr_mass[stars][central] * 1e10 #* u.Msun
    metals = metals[stars][central] / 0.0127 # Zsolar, according to Illustric table A.4
    a = a[stars][central]

    form_time = 2.0/(3.0*H0) * 1./np.sqrt(omegaL) \
                * np.log(np.sqrt(omegaL*1./omegaM*(a)**3) \
                + np.sqrt(omegaL*1./omegaM*(a)**3+1)) \
                * 3.08568e19/3.15576e16 * u.Gyr
    age = timenow-form_time

    met_center_bins = np.array([-2.5, -2.05, -1.75, -1.45, -1.15, -0.85, -0.55, -0.35, -0.25, -0.15, 
                       -0.05, 0.05, 0.15, 0.25, 0.4, 0.5]) # log solar, based on Miles
    #met_center_bins = np.log10(sp.zlegend)
    met_bins = np.empty(met_center_bins.size)#+1)
    half_width = (met_center_bins[1:] - met_center_bins[:-1])/2
    met_bins[:-1] = met_center_bins[:-1] + half_width
    #met_bins[0] = -9
    met_bins[-1] = 9
    z_binner = np.digitize(np.log10(metals), met_bins)

    time_bins = np.arange(0,14.01,0.01)
    time_avg = (time_bins[:-1] + time_bins[1:])/2 # formation time for fsps
    dt = time_bins[1:] - time_bins[:-1] # if we change to unequal bins this supports that

    # one row for each different metallicity's spectrum
    spec_z = np.zeros((met_center_bins.size+1, 5994)) 

    for i in range(1, met_center_bins.size): # garbage metallicities have i = = 0
        sp.params['logzsol'] = met_center_bins[i]
        #print(met_center_bins[i-1])

        # find the SFH for this metallicity
        pop_form = form_time[z_binner==i]
        pop_mass = init_mass[z_binner==i]
        t_binner = np.digitize(pop_form, time_bins)
        sfr = np.array([ pop_mass[t_binner==j].sum()/dt[j] for j in range(dt.size) ])
        sfr /= 1e9 # to Msun/Gyr
        #print(sfr.nonzero())

        if use_inst:
            # Add instantaneous SFR from gas to last bin (i.e., now)
            sfr[-1] = inst_sfr[sub_id]['SFR']

        sp.set_tabular_sfh(time_avg, sfr)
        wave, spec = sp.get_spectrum(tage=14.0)
        spec_z[i] = spec

    full_spec = np.nansum(spec_z, axis=0)
    print("Rank",rank,"writing spectra_{:06d}.txt".format(sub_id));sys.stdout.flush()
    np.savetxt("spectra_{:06d}.txt".format(sub_id), np.vstack((wave, full_spec)))
