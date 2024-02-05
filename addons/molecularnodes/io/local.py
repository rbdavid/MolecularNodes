import bpy
import numpy as np
import warnings
from .. import assembly
from .load import create_molecule
from ..blender import (
    nodes
)

bpy.types.Scene.MN_import_local_path = bpy.props.StringProperty(
    name = 'File', 
    description = 'File path of the structure to open', 
    options = {'TEXTEDIT_UPDATE'}, 
    subtype = 'FILE_PATH', 
    maxlen = 0
    )
bpy.types.Scene.MN_import_local_name = bpy.props.StringProperty(
    name = 'Name', 
    description = 'Name of the molecule on import', 
    options = {'TEXTEDIT_UPDATE'}, 
    default = 'NewMolecule', 
    maxlen = 0
    )


def load(
    file_path,                    
    name = "Name",                      
    centre = False,                    
    del_solvent = True,                    
    style = 'spheres',
    build_assembly = False,
    setup_nodes = True
    ): 
    from biotite import InvalidFileError
    import biotite.structure as struc
    import os
    
    file_path = os.path.abspath(file_path)
    file_ext = os.path.splitext(file_path)[1]
    transforms = None
    if file_ext == '.pdb':
        mol, file = open_structure_local_pdb(file_path)
        try:
            transforms = assembly.pdb.PDBAssemblyParser(file).get_assemblies()
        except InvalidFileError:
            pass

    elif file_ext == '.pdbx' or file_ext == '.cif':
        mol, file = open_structure_local_pdbx(file_path)
        try:
            transforms = assembly.cif.CIFAssemblyParser(file).get_assemblies()
        except InvalidFileError:
            pass
        
        
    else:
        warnings.warn("Unable to open local file. Format not supported.")
    # if bonds chosen but no bonds currently exist (mn.bonds is None)
    # then attempt to find bonds by distance
    if not mol.bonds:
        mol.bonds = struc.connect_via_distances(mol[0], inter_residue=True)
    
    if not (file_ext == '.pdb' and file.get_model_count() > 1):
        file = None
        
    
    mol, coll_frames = create_molecule(
        array = mol,
        name = name,
        file = file,
        calculate_ss = True,
        centre = centre,
        del_solvent = del_solvent
        )
    
    # setup the required initial node tree on the object 
    if setup_nodes:
        nodes.create_starting_node_tree(
            object = mol,
            coll_frames = coll_frames,
            style = style
            )
    
    mol.mn['molecule_type'] = 'local'
    
    if transforms:
        mol['biological_assemblies'] = transforms
    
    if build_assembly:
        nodes.assembly_insert(mol)
    
    return mol

def ss_id_to_numeric(id: str) -> int:
    "Convert the given ids in the mmmCIF file to 1 AH / 2 BS / 3 Loop integers"
    if "HELX" in id:
        return int(1)
    elif "STRN" in id:
        return int(2)
    else:
        return int(3)

class NoSecondaryStructureError(Exception):
    """Raised when no secondary structure is found"""
    pass

def get_ss_mmcif(mol, file):
    import biotite.structure as struc
    
    conf = file.get_category('struct_conf')
    if not conf:
        raise NoSecondaryStructureError
    starts = conf['beg_auth_seq_id'].astype(int)
    ends = conf['end_auth_seq_id'].astype(int)
    chains = conf['end_auth_asym_id'].astype(str)
    id_label = conf['id'].astype(str)
    
    sheet = file.get_category('struct_sheet_range')
    if sheet:
        starts = np.append(starts, sheet['beg_auth_seq_id'].astype(int))
        ends = np.append(ends, sheet['end_auth_seq_id'].astype(int))
        chains = np.append(chains, sheet['end_auth_asym_id'].astype(str))
        id_label = np.append(id_label, np.repeat('STRN', len(sheet['id'])))
    
    id_int = np.array([ss_id_to_numeric(x) for x in id_label])
    lookup = dict()
    for chain in np.unique(chains):
        arrays = []
        mask = (chain == chains)
        start_sub = starts[mask]
        end_sub = ends[mask]
        id_sub = id_int[mask]
        
        for (start, end, id) in zip(start_sub, end_sub, id_sub):
            idx = np.arange(start, end + 1, dtype = int)
            arr = np.zeros((len(idx), 2), dtype = int)
            arr[:, 0] = idx
            arr[:, 1] = 3
            arr[:, 1] = id
            arrays.append(arr)
        
        lookup[chain] =  dict(np.vstack(arrays).tolist())
    
    ss = []
    
    for i, (chain_id, res_id) in enumerate(zip(mol.chain_id, mol.res_id)):
        ss.append(lookup[chain_id].get(res_id, 3))
    
    arr = np.array(ss, dtype = int)
    arr[~struc.filter_amino_acids(mol)] = 0
    return arr

def open_structure_local_pdb(file_path):
    import biotite.structure.io.pdb as pdb
    
    file = pdb.PDBFile.read(file_path)
    
    # returns a numpy array stack, where each array in the stack is a model in the 
    # the file. The stack will be of length = 1 if there is only one model in the file
    mol = pdb.get_structure(file, extra_fields = ['b_factor', 'charge', 'occupancy', 'atom_id'], include_bonds = True)
    return mol, file


def open_structure_local_pdbx(file_path):
    import biotite.structure as struc
    import biotite.structure.io.pdbx as pdbx
    from biotite import InvalidFileError
    
    file = pdbx.PDBxFile.read(file_path)
    
    # returns a numpy array stack, where each array in the stack is a model in the 
    # the file. The stack will be of length = 1 if there is only one model in the file
    
    # Try to get the structure, if no structure exists try to get a small molecule
    try:
        mol  = pdbx.get_structure(file, extra_fields = ['b_factor', 'charge'])
    except InvalidFileError:
        mol = pdbx.get_component(file)
    
    try:
        mol.set_annotation('sec_struct', get_ss_mmcif(mol, file))
    except NoSecondaryStructureError:
        pass

    # pdbx doesn't include bond information apparently, so manually create them here
    if not mol.bonds:
        mol[0].bonds = struc.bonds.connect_via_residue_names(mol[0], inter_residue = True)
    return mol, file

# operator that calls the function to import the structure from a local file
class MN_OT_Import_Protein_Local(bpy.types.Operator):
    bl_idname = "mn.import_protein_local"
    bl_label = "Load"
    bl_description = "Open a local structure file"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return not False

    def execute(self, context):
        scene = context.scene
        file_path = scene.MN_import_local_path
        
        mol = load(
            file_path=file_path, 
            name=scene.MN_import_local_name, 
            centre=scene.MN_import_centre, 
            del_solvent=scene.MN_import_del_solvent, 
            style=scene.MN_import_style, 
            build_assembly=scene.MN_import_build_assembly,
            setup_nodes=scene.MN_import_node_setup
            
            )
        
        # return the good news!
        bpy.context.view_layer.objects.active = mol
        self.report({'INFO'}, message=f"Imported '{file_path}' as {mol.name}")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)

def panel(layout, scene):
    layout.label(text = "Load a Local File", icon='FILE_TICK')
    layout.separator()
    row_name = layout.row(align = False)
    row_name.prop(scene, 'MN_import_local_name')
    row_name.operator('mn.import_protein_local')
    row_import = layout.row()
    row_import.prop(scene, 'MN_import_local_path')
    layout.separator()
    layout.label(text = "Options", icon = "MODIFIER")
    row = layout.row()
    row.prop(scene, 'MN_import_node_setup', text = "")
    col = row.column()
    col.prop(scene, "MN_import_style")
    col.enabled = scene.MN_import_node_setup
    grid = layout.grid_flow()
    grid.prop(scene, 'MN_import_build_assembly')
    grid.prop(scene, 'MN_import_centre', icon_value=0)
    grid.prop(scene, 'MN_import_del_solvent', icon_value=0)