import math
import re
import pandas as pd
import os

from ase.io import read
from pymatgen.core import Composition
from pymatgen.io.cif import CifBlock, CifParser
from pymatgen.symmetry.groups import SpaceGroup
from pymatgen.core.operations import SymmOp
from pymatgen.io.ase import AseAtomsAdaptor
# from periodictable import elements
from pymatgen.core.periodic_table import Element

def get_unit_cell_volume(a, b, c, alpha_deg, beta_deg, gamma_deg):
    alpha_rad = math.radians(alpha_deg)
    beta_rad = math.radians(beta_deg)
    gamma_rad = math.radians(gamma_deg)

    volume = (a * b * c * math.sqrt(1 - math.cos(alpha_rad) ** 2 - math.cos(beta_rad) ** 2 - math.cos(gamma_rad) ** 2 +
                                    2 * math.cos(alpha_rad) * math.cos(beta_rad) * math.cos(gamma_rad)))

    return volume


def get_atomic_props_block_for_formula(formula, oxi=False):
    comp = Composition(formula)
    return get_atomic_props_block(comp, oxi)


def get_atomic_props_block(composition, oxi=False):
    noble_vdw_radii = {
        "He": 1.40,
        "Ne": 1.54,
        "Ar": 1.88,
        "Kr": 2.02,
        "Xe": 2.16,
        "Rn": 2.20,
    }

    allen_electronegativity = {
        "He": 4.16,
        "Ne": 4.79,
        "Ar": 3.24,
    }

    def _format(val):
        return f"{float(val): .4f}"

    def _format_X(elem):
        if math.isnan(elem.X) and str(elem) in allen_electronegativity:
            return allen_electronegativity[str(elem)]
        return _format(elem.X)

    def _format_radius(elem):
        if elem.atomic_radius is None and str(elem) in noble_vdw_radii:
            return noble_vdw_radii[str(elem)]
        return _format(elem.atomic_radius)

    props = {str(el): (_format_X(el), _format_radius(el), _format(el.average_ionic_radius))
             for el in sorted(composition.elements)}

    data = {}
    data["_atom_type_symbol"] = list(props)
    data["_atom_type_electronegativity"] = [v[0] for v in props.values()]
    data["_atom_type_radius"] = [v[1] for v in props.values()]
    # use the average ionic radius
    data["_atom_type_ionic_radius"] = [v[2] for v in props.values()]

    loop_vals = [
        "_atom_type_symbol",
        "_atom_type_electronegativity",
        "_atom_type_radius",
        "_atom_type_ionic_radius"
    ]

    if oxi:
        symbol_to_oxinum = {str(el): (float(el.oxi_state), _format(el.ionic_radius)) for el in sorted(composition.elements)}
        data["_atom_type_oxidation_number"] = [v[0] for v in symbol_to_oxinum.values()]
        # if we know the oxidation state of the element, use the ionic radius for the given oxidation state
        data["_atom_type_ionic_radius"] = [v[1] for v in symbol_to_oxinum.values()]
        loop_vals.append("_atom_type_oxidation_number")

    loops = [loop_vals]

    return str(CifBlock(data, loops, "")).replace("data_\n", "")

def replace_symmetry_operators(cif_str, space_group_symbol):
    space_group = SpaceGroup(space_group_symbol)
    symmetry_ops = space_group.symmetry_ops

    loops = []
    data = {}
    symmops = []
    #breakpoint()
    for op in symmetry_ops:
        v = op.translation_vector
        symmops.append(SymmOp.from_rotation_and_translation(op.rotation_matrix, v))
    #breakpoint()
    ops = [op.as_xyz_str() for op in symmops] #[op.as_xyz_string() for op in symmops]
    data["_symmetry_equiv_pos_site_id"] = [f"{i}" for i in range(1, len(ops) + 1)]
    data["_symmetry_equiv_pos_as_xyz"] = ops

    loops.append(["_symmetry_equiv_pos_site_id", "_symmetry_equiv_pos_as_xyz"])

    symm_block = str(CifBlock(data, loops, "")).replace("data_\n", "")

    pattern = r"(loop_\n_symmetry_equiv_pos_site_id\n_symmetry_equiv_pos_as_xyz\n1 'x, y, z')"
    cif_str_updated = re.sub(pattern, symm_block, cif_str)

    return cif_str_updated


def extract_space_group_symbol(cif_str):
    match = re.search(r"_symmetry_space_group_name_H-M\s+('([^']+)'|(\S+))", cif_str)
    if match:
        return match.group(2) if match.group(2) else match.group(3)
    raise Exception(f"could not extract space group from:\n{cif_str}")


def extract_numeric_property(cif_str, prop, numeric_type=float):
    match = re.search(rf"{prop}\s+([.0-9]+)", cif_str)
    if match:
        return numeric_type(match.group(1))
    raise Exception(f"could not find {prop} in:\n{cif_str}")


def extract_volume(cif_str):
    return extract_numeric_property(cif_str, "_cell_volume")


def extract_formula_units(cif_str):
    return extract_numeric_property(cif_str, "_cell_formula_units_Z", numeric_type=int)


def extract_data_formula(cif_str):
    # breakpoint()
    match = re.search(r"data_([A-Za-z0-9]+)\n", cif_str) 
    #åbreakpoint()
    if match is None:
        match = re.search(r"data_([A-Za-z0-9<>-]+_miller_[0-9]+)", cif_str) # update to process miller index incorporated data
    # breakpoint()
    if match:
        return match.group(1)
    raise Exception(f"could not find data_ in:\n{cif_str}")

def extract_ads_bulk_symbols(cif_str):
    match = re.search(r"data_([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)_([A-Za-z0-9]+(?:_[0-9]+)?)", cif_str)
    if match:
        # Extract bulk_symbols and ads_symbols from the matched groups
        bulk_symbols, ads_symbols = match.group(1), match.group(2)
        # Remove any spaces from the extracted symbols
        bulk_symbols = bulk_symbols.replace(" ", "")
        ads_symbols = ads_symbols.replace(" ", "")
        return bulk_symbols, ads_symbols
    
    # Raise an exception if no valid 'data_' line is found
    raise Exception(f"Could not find data_ in:\n{cif_str}")


def extract_formula_nonreduced(cif_str):
    match = re.search(r"_chemical_formula_sum\s+('([^']+)'|(\S+))", cif_str)
    if match:
        return match.group(2) if match.group(2) else match.group(3)
    raise Exception(f"could not extract _chemical_formula_sum value from:\n{cif_str}")

def extract_formula_based_on_coords(cif_str):
    parser = CifParser.from_str(cif_str)
    cif_data = parser.as_dict()

    # Extracting formula from CIF file by counting listed atoms
    atom_counts = {}
    for site in cif_data[list(cif_data.keys())[0]]["_atom_site_type_symbol"]:
        atom = site.split()[0]
        if atom in atom_counts:
            atom_counts[atom] += 1
        else:
            atom_counts[atom] = 1
    formula_from_atoms = ''.join([f"{atom}{count}" for atom, count in atom_counts.items()])

    return formula_from_atoms

def semisymmetrize_cif(cif_str):
    return re.sub(
        r"(_symmetry_equiv_pos_as_xyz\n)(.*?)(?=\n(?:\S| \S))",
        r"\1  1  'x, y, z'",
        cif_str,
        flags=re.DOTALL
    )

# def replace_data_formula_with_symbols(cif_str, bulk_symbols, ads_symbols):
#     pattern_2 = r"(data_)(.*?)(\n)"
#     try:
#         modified_cif = re.sub(pattern_2, r'\1' + str(bulk_symbols) + '_' + str(ads_symbols) + r'\3', cif_str)
#         return modified_cif
#     except:
#         raise Exception(f"Failed at conversion: {cif_str}")

def get_electronegativity(symbol):
    try:
        return Element(symbol).X #elements.symbol(str(symbol)).electronegativity()
    except:
        return 0

def sort_by_electronegativity(symbols):
    return sorted(symbols, key=lambda x: get_electronegativity(x[0]))

def get_string_from_symbols(bulk_symbols, ads_symbols):
    # Remove unnecessary characters
    bulk_symbols = bulk_symbols.replace(' ', '')
    ads_symbols = ads_symbols.replace('*', '')
    ads_symbols = ads_symbols.replace(' ', '')
    
    # Parse the elements and their amounts from the bulk and adsorbed symbols
    bulk_elems = Composition(bulk_symbols).get_el_amt_dict().items()
    ads_elems = Composition(ads_symbols).get_el_amt_dict().items()

    # Create tuples of (symbol, count) for bulk and adsorbed elements
    bulk_symbols = [(symbol, count) for symbol, count in bulk_elems]
    ads_symbols = [(symbol, count) for symbol, count in ads_elems]

    # Sort elements based on electronegativity
    bulk_sorted = sort_by_electronegativity(bulk_symbols)
    ads_sorted = sort_by_electronegativity(ads_symbols)

    # Create the output string for bulk and adsorbed symbols
    bulk_str = ''.join([f"{symbol}{int(count) if count > 1 else ''}" for symbol, count in bulk_sorted])
    ads_str = ''.join([f"{symbol}{int(count) if count > 1 else ''}" for symbol, count in ads_sorted])
    return bulk_str, ads_str


def replace_data_formula_with_catberta_string(cif_str, ads_symbols, bulk_symbols, miller_index):
    pattern_formula = r"_chemical_formula_sum\s+'(.+?)'\n"
    pattern_data = r"(data_)(.*?)(\n)"

    bulk_symbols = bulk_symbols.replace(' ', '')
    bulk_elems = Composition(bulk_symbols).get_el_amt_dict().items()

    match = re.search(pattern_formula, cif_str)
    if not match:
        raise Exception("Chemical formula not found in CIF string")

    formula = match.group(1)
    element_counts = {}
    for element in re.findall(r'([A-Z][a-z]?)(\d*)', formula):
        symbol, count = element
        count = int(count) if count else 1
        element_counts[symbol] = count

    bulk_symbols = [(symbol, element_counts.get(symbol, 1)) for symbol, _ in bulk_elems]

    bulk_sorted = sort_by_electronegativity(bulk_symbols)

    bulk_str = ''.join([f"{symbol}{int(count)}" for symbol, count in bulk_sorted])
    ads_str = ads_symbols.replace('*', '')
    miller_str = str(miller_index).replace(',', '')
    
    modified_cif = re.sub(pattern_data, r'\1' + ads_str + '</s>' + bulk_str + ' ' + miller_str + r'\3', cif_str)
    
    
    return modified_cif

def replace_data_formula_with_symbols(cif_str, bulk_symbols, ads_symbols):
    pattern_formula = r"_chemical_formula_sum\s+'(.+?)'\n"
    pattern_data = r"(data_)(.*?)(\n)"
    
    # preprocessing
    # bulk_symbols = bulk_symbols.replace(' ', '')
    # ads_symbols = ads_symbols.replace('*', '')
    # #breakpoint()
    # bulk_elems = Composition(bulk_symbols).get_el_amt_dict().items()
    # ads_elems = Composition(ads_symbols).get_el_amt_dict().items()
    # #breakpoint()
    # match = re.search(pattern_formula, cif_str)
    # if not match:
    #     raise Exception("Chemical formula not found in CIF string")

    # formula = match.group(1)
    # element_counts = {}
    # for element in re.findall(r'([A-Z][a-z]?)(\d*)', formula):
    #     symbol, count = element
    #     count = int(count) if count else 1
    #     element_counts[symbol] = count

    # bulk_symbols = [(symbol, element_counts.get(symbol, 1)) for symbol, _ in bulk_elems]
    # ads_symbols = [(symbol, element_counts.get(symbol, 1)) for symbol, _ in ads_elems]

    # bulk_sorted = sort_by_electronegativity(bulk_symbols)
    # ads_sorted = sort_by_electronegativity(ads_symbols)

    # bulk_str = ''.join([f"{symbol}{int(count)}" for symbol, count in bulk_sorted])
    # ads_str = ''.join([f"{symbol}{int(count)}" for symbol, count in ads_sorted])
    # # breakpoint()
    # modified_cif = re.sub(pattern_data, r'\1' + bulk_str + '-' + ads_str + r'\3', cif_str)
    
    # if we decide to use adsorbate SMILES, we can use the following code
    bulk_symbols = bulk_symbols.replace(' ', '')
    
    #breakpoint()
    bulk_elems = Composition(bulk_symbols).get_el_amt_dict().items()
    #breakpoint()
    match = re.search(pattern_formula, cif_str)
    if not match:
        raise Exception("Chemical formula not found in CIF string")

    formula = match.group(1)
    element_counts = {}
    for element in re.findall(r'([A-Z][a-z]?)(\d*)', formula):
        symbol, count = element
        count = int(count) if count else 1
        element_counts[symbol] = count

    bulk_symbols = [(symbol, element_counts.get(symbol, 1)) for symbol, _ in bulk_elems]

    bulk_sorted = sort_by_electronegativity(bulk_symbols)

    bulk_str = ''.join([f"{symbol}{int(count)}" for symbol, count in bulk_sorted])
    ads_str = ads_symbols.replace('*', '')
    
    modified_cif = re.sub(pattern_data, r'\1' + bulk_str + '-' + ads_str + r'\3', cif_str)
    
    
    return modified_cif

def replace_data_formula_with_nonreduced_formula(cif_str, miller_index=None):
    pattern = r"_chemical_formula_sum\s+(.+)\n"
    pattern_2 = r"(data_)(.*?)(\n)"
    match = re.search(pattern, cif_str)
    if match:
        chemical_formula = match.group(1)
        chemical_formula = chemical_formula.replace("'", "").replace(" ", "")
        # Format miller_index if it's not None; otherwise, leave it as an empty string
        if miller_index is not None:
            # Ensure miller_index is a string and formatted correctly
            #miller_index_str = "_" + "_".join(map(str, miller_index)) if isinstance(miller_index, (list, tuple)) else "_" + str(miller_index)
            #miller_index_str = "_miller_" + str(miller_index) if isinstance(miller_index, (list, tuple)) else "_" + str(miller_index)
            miller_index_str = "_miller_" + "".join(str(abs(x)) for x in miller_index) if isinstance(miller_index, (list, tuple)) else "_" + str(miller_index)
        else:
            miller_index_str = ""
        # Include miller_index in the replacement string if available
        modified_cif = re.sub(pattern_2, r'\1' + chemical_formula + miller_index_str + r'\3', cif_str)
        # modified_cif = re.sub(pattern_2, r'\1' + chemical_formula + r'\3', cif_str)

        return modified_cif
    else:
        raise Exception(f"Chemical formula not found {cif_str}")


def add_atomic_props_block(cif_str, oxi=False):
    comp = Composition(extract_formula_nonreduced(cif_str))

    block = get_atomic_props_block(composition=comp, oxi=oxi)

    # the hypothesis is that the atomic properties should be the first thing
    #  that the model must learn to associate with the composition, since
    #  they will determine so much of what follows in the file
    pattern = r"_symmetry_space_group_name_H-M"
    match = re.search(pattern, cif_str)

    if match:
        start_pos = match.start()
        modified_cif = cif_str[:start_pos] + block + "\n" + cif_str[start_pos:]
        return modified_cif
    else:
        raise Exception(f"Pattern not found: {cif_str}")


def remove_atom_props_block(cif):
    pattern = re.compile(r"(data_[^\n]*\n)loop_[\s\S]*?(_symmetry_space_group_name_H-M)", re.MULTILINE)
    new_cif = re.sub(pattern, r"\1\2", cif)
    return new_cif


def round_numbers(cif_str, decimal_places=4):
    # Pattern to match a floating point number in the CIF file
    # It also matches numbers in scientific notation
    pattern = r"[-+]?\d*\.\d+([eE][-+]?\d+)?"

    # Function to round the numbers
    def round_number(match):
        number_str = match.group()
        number = float(number_str)
        # Check if number of digits after decimal point is less than 'decimal_places'
        if len(number_str.split('.')[-1]) <= decimal_places:
            return number_str
        rounded = round(number, decimal_places)
        return format(rounded, '.{}f'.format(decimal_places))

    # Replace all occurrences of the pattern using a regex sub operation
    cif_string_rounded = re.sub(pattern, round_number, cif_str)

    return cif_string_rounded


def array_split(arr, num_splits):
    split_size, remainder = divmod(len(arr), num_splits)
    splits = []
    start = 0
    for i in range(num_splits):
        end = start + split_size + (i < remainder)
        splits.append(arr[start:end])
        start = end
    return splits


def embeddings_from_csv(embedding_csv):
    df = pd.read_csv(embedding_csv)
    elements = list(df["element"])
    df.drop(["element"], axis=1, inplace=True)
    embeds_array = df.to_numpy()
    embedding_data = {
        elements[i]: embeds_array[i] for i in range(len(embeds_array))
    }
    return embedding_data


def load_labels(sid, directory):
    # preprocess the system id
    sid = re.match(r'random\d+', sid).group()
    # for directory in directories:
    label_file = os.path.join(directory, f'{sid}.traj')
    if os.path.exists(label_file):
        atoms = read(label_file, '-1')
        structure = AseAtomsAdaptor.get_structure(atoms)
        return structure, atoms.get_potential_energy()
    raise FileNotFoundError(f'Trajectory file for system ID {sid} not found in any of the specified directories.')

# def sid_to_structure(sid, directories):
#     # Preprocess the system ID
#     sid = re.match(r'random\d+', sid).group()
    
#     # Iterate over each directory in the list
#     for directory in directories:
#         # Search for trajectory file in the current directory
#         traj_file = os.path.join(directory, f'{sid}.traj')
#         if os.path.exists(traj_file):
#             # Read the last frame of the trajectory using ASE
#             atoms = read(traj_file, '-1')  # Read the last frame
#             # Convert ASE atoms to Pymatgen Structure
#             structure = AseAtomsAdaptor.get_structure(atoms)
#             return structure  # Return structure if found
    
#     # If trajectory file is not found in any directory
#     raise FileNotFoundError(f'Trajectory file for system ID {sid} not found in any of the specified directories.')
