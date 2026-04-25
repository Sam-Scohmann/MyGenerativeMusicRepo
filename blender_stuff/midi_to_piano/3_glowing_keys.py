import bpy


def setup_mix_node_drivers():
    """
    For each selected object:
    - Make material unique if it has multiple users
    - Find 'note_on' custom property on the object
    - Find MIX node in the material
    - Add driver to MIX node's input[0] default_value
    """

    for obj in bpy.context.selected_objects:
        # Skip objects without materials
        if not obj.material_slots:
            print(f"Skipping '{obj.name}': No materials found")
            continue

        # Check if object has 'note_on' custom property
        if 'note_on' not in obj:
            print(f"Skipping '{obj.name}': No 'note_on' custom property found")
            continue

        for slot in obj.material_slots:
            mat = slot.material

            if not mat or not mat.use_nodes:
                continue

            # Make material unique if it has multiple users
            if mat.users > 1:
                mat = mat.copy()
                slot.material = mat
                print(f"Made material '{mat.name}' unique for object '{obj.name}'")

            # Find MIX type nodes (Mix Shader or MixRGB)
            mix_nodes = [node for node in mat.node_tree.nodes
                         if node.type == 'MIX' or node.type == 'MIX_SHADER']

            if not mix_nodes:
                print(f"No MIX node found in material '{mat.name}' for object '{obj.name}'")
                continue

            # Add driver to each MIX node's first input
            for mix_node in mix_nodes:
                # Check if input[0] exists
                if len(mix_node.inputs) == 0:
                    continue

                input_socket = mix_node.inputs[0]

                # Remove existing driver if any
                #                if input_socket.animation_data:
                #                    input_socket.animation_data_clear()

                # Create driver
                driver = input_socket.driver_add('default_value').driver
                driver.type = 'AVERAGE'

                # Add variable
                var = driver.variables.new()
                var.name = 'note_on'
                var.type = 'SINGLE_PROP'

                # Target the object
                target = var.targets[0]
                target.id_type = 'OBJECT'
                target.id = obj
                target.data_path = '["note_on"]'

                print(f"Added driver to '{mix_node.name}' in material '{mat.name}' for object '{obj.name}'")


# Run the function
setup_mix_node_drivers()