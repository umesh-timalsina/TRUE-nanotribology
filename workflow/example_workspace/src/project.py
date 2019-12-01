
import textwrap
import logging
import os
import pathlib

import flow
import numpy as np
import scipy

from flow import FlowProject, environments
from foyer import Forcefield
from mbuild.formats.lammpsdata import write_lammpsdata
from mbuild.lib.atoms import H
from ctools.fileio import write_monolayer_ndx, read_ndx
from ctools.lib.chains import Alkylsilane
from ctools.lib.recipes import DualSurface, SilicaInterface, SurfaceMonolayer
from index_groups import generate_index_groups

class SimpleProject(flow.environment.DefaultTorqueEnvironment):
    template = "rahman.sh"

class Project(FlowProject):
    pass
    
@Project.operation
@Project.post.isfile("init.top")
@Project.post.isfile("init.gro")
@Project.post.isfile("init.lammps")
@Project.post.isfile("init.ndx")
def initialize_system(job):
    """ Generate the monolayer surfaces, parametrize, save LAMMPS, GRO, TOP.
    """
    
    """
    -------------------------------
    Declare the backbone dictionary
    -------------------------------
    """
    backbone_dict = {'Alkylsilane':Alkylsilane}
    """
    ---------------------------
    Read statepoint information
    ---------------------------
    """
    chainlength = job.statepoint()["chainlength"]
    backbone = backbone_dict[job.statepoint()["backbone"]]
    seed = job.statepoint()["seed"]
    pattern_type = job.statepoint()["pattern_type"]
    terminal_group = job.statepoint()["terminal_group"]
    num_chains = job.statepoint()["n"]

    """
    -----------------------------------
    Generate amorphous silica interface
    -----------------------------------
    """
    surface_a = SilicaInterface(thickness=1.2, seed=seed)
    surface_b = SilicaInterface(thickness=1.2, seed=seed)

    """
    ------------------------------------------------------
    Generate prototype of functionalized alkylsilane chain
    ------------------------------------------------------
    """
    chain_prototype_A = backbone(
        chain_length=chainlength, terminal_group=terminal_group)
    chain_prototype_B = backbone(
        chain_length=chainlength, terminal_group=terminal_group)
    """
    ----------------------------------------------------------
    Create monolayer on surface, backfilled with hydrogen caps
    ----------------------------------------------------------
    """
    # bottom monolayer is backfilled with the other terminal group
    # num_chains = num_chains * a_fraction
    monolayer_a = SurfaceMonolayer(
        surface=surface_a,
        chains=chain_prototype_A,
        n_chains=num_chains,
        seed=seed,
        backfill=H(),
        rotate=False,
    )
    monolayer_a.name = "Bottom"
    monolayer_b = SurfaceMonolayer(
        surface=surface_b,
        chains=chain_prototype_B,
        n_chains=num_chains,
        seed=seed,
        backfill=H(),
        rotate=False,
    )
    monolayer_b.name = "Top"

    """
    ----------------------
    Create dual monolayers
    ----------------------
    """
    dual_monolayer = DualSurface(
        bottom=monolayer_a, top=monolayer_b, separation=2.0
    )

    """
    --------------------------------------------------------
    Make sure box is elongated in z to be pseudo-2D periodic
    --------------------------------------------------------
    """
    box = dual_monolayer.boundingbox
    dual_monolayer.periodicity += np.array([0, 0, 5.0 * box.lengths[2]])

    """
    -------------------------------------------------------------------
    - Save to .GRO, .TOP, and .LAMMPS formats
    - Atom-type the system using Foyer, with parameters from the OPLS
    force field obtained from GROMACS. Parameters are located in a
    Foyer XML file in the `atools` git repo, with references provided
    as well as notes where parameters have been added or altered to
    reflect the literature.
    -------------------------------------------------------------------
    """
    # path for project root dir
    proj = signac.get_project()
    forcefield_filepath = pathlib.Path(
        proj.root_directory() + "/src/util/forcefield/oplsaa.xml"
    )
    # change into job directoryA
    _switch_dir(job)
    logging.info("at dir: {}".format(job.ws))
    dual_monolayer.save("init.gro", residues=["Top", "Bottom"], overwrite=True)

    if not (
        job.isfile("init.top")
        and job.isfile("init.lammps")
        and job.isfile("init.gro")
    ):

        structure = dual_monolayer.to_parmed(
            box=None, residues=["Top", "Bottom"]
        )
        ff = Forcefield(forcefield_files=forcefield_filepath.as_posix())
        structure = ff.apply(structure)
        structure.combining_rule = "geometric"

        structure.save("init.top", overwrite=True)
        write_lammpsdata(filename="init.lammps", structure=structure)

        """
        --------------------------------------
        Specify index groups and write to file
        --------------------------------------
        """
    index_groups = generate_index_groups(
        system=dual_monolayer,
        terminal_group=terminal_group,
        freeze_thickness=0.5,
    )
    write_monolayer_ndx(rigid_groups=index_groups, filename="init.ndx")


def _switch_dir(job):
    p = pathlib.Path(job.workspace())
    os.chdir(str(p.absolute()))
