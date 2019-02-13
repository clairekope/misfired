import h5py
import fsps
import pickle
import sys
import os
import numpy as np
import matplotlib.pyplot as plt
import astropy.units as u
# prep MPI environnment and import scatter_work(), get(), periodic_centering(),
# CLI args container, url_dset, url_sbhalos, folder
from utilities import *
from glob import glob

inst = args.inst_sfr
dust = args.dusty

sp = fsps.StellarPopulation(zcontinuous=1, sfh=3)

sp.params['add_agb_dust_model'] = True 
sp.params['add_dust_emission'] = True if dust else False
sp.params['add_igm_absorption'] = False
sp.params['add_neb_emission'] = True
sp.params['add_neb_continuum'] = True
sp.params['add_stellar_remnants'] = False
    
sp.params['dust_type'] = 0 # Charlot & Fall type; parameters from Torrey+15
sp.params['dust_tesc'] = np.log10(3e7)
sp.params['dust1'] = 1 if dust else 0.0
sp.params['dust2'] = 1.0/3.0 if dust else 0.0

sp.params['imf_type'] = 1 # Chabrier (2003)


if rank==0:
    with open(folder+"cut3_g-r.pkl","rb") as f:
        sample = pickle.load(f)
    sub_list = np.array([k for k in sample.keys()])
    if inst:
        with open(folder+"cut3_g-r_gas_info.pkl","rb") as f:
            inst_sfr = pickle.load(f)

    for f in glob(folder+"spectra/{}inst/{}dust/*".format(
                                                   "no_" if not inst else "",
                                                   "no_" if not dust else "")):
        os.remove(f)

else:
    sample = {}
    sub_list = None
    if inst:
        inst_sfr = {}
                                   
if inst:
    inst_sfr = comm.bcast(inst_sfr, root=0)
my_subs = scatter_work(sub_list, rank, size)

boxsize = get(url_dset)['boxsize']
z = get(url_dset + "snapshots/103")['redshift']
a0 = 1/(1+z)

H0 = 0.704 * 100
omegaM = 0.2726
omegaL = 0.7274
timenow = 2.0/(3.0*H0) * 1./np.sqrt(omegaL) \
          * np.log(np.sqrt(omegaL*1./omegaM*a0**3) \
          + np.sqrt(omegaL*1./omegaM*a0**3+1))\
          * 3.08568e19/3.15576e16 * u.Gyr

met_center_bins = np.array([-2.5, -2.05, -1.75, -1.45, -1.15, -0.85, -0.55,
                            -0.35, -0.25, -0.15, -0.05, 0.05, 0.15, 0.25,
                            0.4, 0.5]) # log solar, based on Miles

met_bins = np.empty(met_center_bins.size)
half_width = (met_center_bins[1:] - met_center_bins[:-1])/2
met_bins[:-1] = met_center_bins[:-1] + half_width
met_bins[-1] = 9

time_bins = np.arange(0, timenow.value+0.01, 0.01) # Gyr
time_avg = (time_bins[:-1] + time_bins[1:])/2 # formation time for fsps
dt = time_bins[1:] - time_bins[:-1] # if we change to unequal bins this supports that

# Iterate
# Because scattered arrays have to be the same size, they are padded with -1
good_ids = np.where(my_subs > -1)[0]

for sub_id in my_subs[good_ids]:
    if inst:
        if sub_id not in inst_sfr: # it doesnt have gas!
            continue   

    ### ILLUSTRIS ###                             
    sub = get(url_sbhalos + str(sub_id))
    
    file = folder+"stellar_cutouts/cutout_{}.hdf5".format(sub_id)
    with h5py.File(file) as f:
        coords = f['PartType4']['Coordinates'][:,:]
        a = f['PartType4']['GFM_StellarFormationTime'][:] # as scale factor
        init_mass = f['PartType4']['GFM_InitialMass'][:]
        metals = f['PartType4']['GFM_Metallicity'][:]
    #################

    stars = a > 0

    x = coords[:,0][stars] # throw out wind particles (a < 0)
    y = coords[:,1][stars]
    z = coords[:,2][stars]
    x_rel = periodic_centering(x, sub['pos_x'], boxsize) * u.kpc * a0/0.704
    y_rel = periodic_centering(y, sub['pos_y'], boxsize) * u.kpc * a0/0.704
    z_rel = periodic_centering(z, sub['pos_z'], boxsize) * u.kpc * a0/0.704
    r = np.sqrt(x_rel**2 + y_rel**2 + z_rel**2)

    central = r < 2*u.kpc

    init_mass = init_mass[stars][central] * 1e10/0.704 #* u.Msun
    metals = metals[stars][central] / 0.0127 # Zsolar, according to Illustris table A.4
    a = a[stars][central]

    form_time = 2.0/(3.0*H0) * 1./np.sqrt(omegaL) \
                * np.log(np.sqrt(omegaL*1./omegaM*(a)**3) \
                + np.sqrt(omegaL*1./omegaM*(a)**3+1)) \
                * 3.08568e19/3.15576e16 * u.Gyr

    z_binner = np.digitize(np.log10(metals), met_bins)

    # one row for each different metallicity's spectrum
    spec_z = np.zeros((met_center_bins.size+1, 5994)) 

    for i in range(1, met_center_bins.size): # garbage metallicities have i = = 0
        sp.params['logzsol'] = met_center_bins[i]

        # find the SFH for this metallicity
        pop_form = form_time[z_binner==i]
        pop_mass = init_mass[z_binner==i]
        t_binner = np.digitize(pop_form, time_bins)
        sfr = np.array([ pop_mass[t_binner==j].sum()/dt[j] for j in range(dt.size) ])
        sfr /= 1e9 # to Msun/yr

        if inst:
            # Add instantaneous SFR from gas to last bin (i.e., now)
            sfr[-1] += inst_sfr[sub_id]['inner_SFR'].value # Msun/yr

        sp.set_tabular_sfh(time_avg, sfr)
        wave, spec = sp.get_spectrum(tage=timenow.value)
        spec_z[i] = spec

    full_spec = np.nansum(spec_z, axis=0)
    print("Rank",rank,"writing spectra_{:06d}.txt".format(sub_id));sys.stdout.flush()
    np.savetxt(folder+"spectra/{}inst/{}dust/spectra_{:06d}.txt".format(
                                                        "no_" if not inst else "",
                                                        "no_" if not dust else "",
                                                        sub_id),
               np.vstack((wave, full_spec)))
