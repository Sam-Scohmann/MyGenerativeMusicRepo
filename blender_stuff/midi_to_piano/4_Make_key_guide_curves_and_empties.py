# import bpy
# import bmesh
# from mathutils import Vector

# def raycast_down_from_spline():
#    """
#    For each spline in the active curve object:
#    - Raycast downward from points 0.2m above each spline point
#    - Get the object hit below
#    - Store spline points in dictionary with key = hit_object_name + "Guide"
#    - Then separate and rename each spline
#    """
#
#    # Dictionary to store splines
#    named_splines = {}
#
#    # Get the active object (should be a curve)
#    curve_obj = bpy.context.active_object
#
#    if not curve_obj or curve_obj.type != 'CURVE':
#        print("Error: Please select a curve object first!")
#        return
#
#    # Ensure we're in object mode
#    bpy.ops.object.mode_set(mode='OBJECT')
#
#    # Get all splines from the curve
#    splines = curve_obj.data.splines
#
#    if len(splines) == 0:
#        print("Error: Curve has no splines!")
#        return
#
#    print(f"Processing {len(splines)} splines...")
#
#    # Store original spline data before modifying
#    spline_data = []
#
#    for spline_idx, spline in enumerate(splines):
#        # Get all points from this spline
#        points = []
#
#        if spline.type == 'BEZIER':
#            # For bezier splines, get bezier points
#            for bp in spline.bezier_points:
#                points.append(bp.co.copy())
#        else:
#            # For poly/nurbs splines, get regular points
#            for point in spline.points:
#                points.append(point.co.copy())
#
#        if not points:
#            continue
#
#        # Raycast from 0.2m above each point
#        hit_objects = []
#
#        for point in points:
#            # Convert point from local to world coordinates
#            world_point = curve_obj.matrix_world @ point
#
#            # Start position: 0.2m above the point
#            ray_origin = world_point + Vector((0, 0, 0.2))
#
#            # Ray direction: straight down
#            ray_direction = Vector((0, 0, -1))
#
#            # Perform raycast
#            hit, hit_location, hit_normal, hit_index, hit_object, matrix = bpy.context.scene.ray_cast(
#                bpy.context.view_layer.depsgraph,
#                ray_origin,
#                ray_direction
#            )
#
#            if hit:
#                hit_objects.append(hit_object.name)
#            else:
#                hit_objects.append(None)
#                print(f"  Warning: No hit from point {world_point}")
#
#        # Determine the most common hit object (or use first valid hit)
#        valid_hits = [h for h in hit_objects if h is not None]
#        if valid_hits:
#            # Use the most frequent hit object
#            hit_name = max(set(valid_hits), key=valid_hits.count)
#        else:
#            print(f"  Warning: Spline {spline_idx} hit nothing, skipping")
#            continue
#
#        # Create the dictionary key
#        dict_key = hit_name + "Guide"
#
#        # Store in dictionary (use list of points in world space)
#        if dict_key not in named_splines:
#            named_splines[dict_key] = []
#
#        # Add all points from this spline (as world coordinates)
#        world_points = [curve_obj.matrix_world @ p for p in points]
#        named_splines[dict_key].append(world_points)
#
#        # Store original spline data for later separation
#        spline_data.append({
#            'index': spline_idx,
#            'key': dict_key,
#            'points': points,
#            'world_points': world_points,
#            'spline_type': spline.type
#        })
#
#        print(f"  Spline {spline_idx} → {dict_key}")
#
#    print(f"\nFound {len(named_splines)} unique guide objects:")
#    for key, splines_list in named_splines.items():
#        print(f"  • {key}: {len(splines_list)} spline(s)")
#
#    # Now separate each spline into its own curve object
#    separated_objects = []
#
#    for key, spline_group in named_splines.items():
#        # Create new curve data
#        new_curve_data = bpy.data.curves.new(name=key, type='CURVE')
#        new_curve_data.dimensions = '3D'
#
#        # Add each spline in this group to the new curve
#        for spline_points in spline_group:
#            # Create a new spline
#            new_spline = new_curve_data.splines.new(type='POLY')
#
#            # Set number of points
#            new_spline.points.add(len(spline_points) - 1)
#
#            # Assign point coordinates
#            for i, point in enumerate(spline_points):
#                # Convert to 4D coordinates (x, y, z, w)
#                new_spline.points[i].co = (point.x, point.y, point.z, 1.0)
#
#        # Create new curve object
#        new_curve_obj = bpy.data.objects.new(name=key, object_data=new_curve_data)
#
#        # Link to collection
#        bpy.context.collection.objects.link(new_curve_obj)
#        separated_objects.append(new_curve_obj)
#
#        print(f"Created: {key}")
#
#    # Optional: Delete or hide the original curve
#    original_name = curve_obj.name
#    curve_obj.hide_set(True)
#    print(f"\nOriginal curve '{original_name}' has been hidden")
#
#    # Select all new objects
#    bpy.ops.object.select_all(action='DESELECT')
#    for obj in separated_objects:
#        obj.select_set(True)
#
#    if separated_objects:
#        bpy.context.view_layer.objects.active = separated_objects[0]
#        print(f"\n✓ Success! Created {len(separated_objects)} new curve objects")
#        print("  (Original curve has been hidden - unhide it if needed)")
#
#    return named_splines, separated_objects

## Run the function
# if __name__ == "__main__":
#    named_splines, new_objects = raycast_down_from_spline()
#
#    # Print summary
#    if named_splines:
#        print("\n" + "="*50)
#        print("DICTIONARY CONTENTS:")
#        print("="*50)
#        for key, splines in named_splines.items():
#            print(f"\n{key}:")
#
#            spline_ob = bpy.data.objects.get(key)
#            key_ob = bpy.data.objects.get(key.replace('Guide', ''))
#
#            if spline_ob and key_ob:
#                spline_ob.parent = key_ob
#                #create empty
#                emp = bpy.data.objects.new(name= key+'Emp', object_data=None)
#                bpy.context.collection.objects.link(emp)
#                point = splines[0][0]
#                emp.location = (point.x, point.y, point.z)
#                emp.empty_display_size = 0.02
#
#            for i, spline_points in enumerate(splines):
#                print(f"  Spline {i}: {len(spline_points)} points")
#                for j, point in enumerate(spline_points[:3]):  # Show first 3 points
#                    print(f"    Point {j}: ({point.x:.3f}, {point.y:.3f}, {point.z:.3f})")
#                if len(spline_points) > 3:
#                    print(f"    ... and {len(spline_points)-3} more points")


import bpy
import bmesh
from mathutils import Vector, Matrix


def raycast_down_from_spline():
    """
    For each spline in the active curve object:
    - Raycast downward from points 0.2m above each spline point
    - Get the object hit below
    - Store spline points in dictionary with key = hit_object_name + "Guide"
    - Then separate and rename each spline
    """

    # Dictionary to store splines
    named_splines = {}

    # Get the active object (should be a curve)
    curve_obj = bpy.context.active_object

    if not curve_obj or curve_obj.type != 'CURVE':
        print("Error: Please select a curve object first!")
        return None, None

    # Ensure we're in object mode
    bpy.ops.object.mode_set(mode='OBJECT')

    # Get all splines from the curve
    splines = curve_obj.data.splines

    if len(splines) == 0:
        print("Error: Curve has no splines!")
        return None, None

    print(f"Processing {len(splines)} splines...")

    # Store original spline data before modifying
    spline_data = []

    for spline_idx, spline in enumerate(splines):
        # Get all points from this spline
        points = []

        if spline.type == 'BEZIER':
            # For bezier splines, get bezier points
            for bp in spline.bezier_points:
                points.append(bp.co.copy())
        else:
            # For poly/nurbs splines, get regular points
            for point in spline.points:
                points.append(point.co.copy())

        if not points:
            continue

        # Raycast from 0.2m above each point
        hit_objects = []

        for point in points:
            # Convert point from local to world coordinates
            world_point = curve_obj.matrix_world @ point

            # Start position: 0.2m above the point
            ray_origin = world_point + Vector((0, 0, 0.2))

            # Ray direction: straight down
            ray_direction = Vector((0, 0, -1))

            # Perform raycast
            hit, hit_location, hit_normal, hit_index, hit_object, matrix = bpy.context.scene.ray_cast(
                bpy.context.view_layer.depsgraph,
                ray_origin,
                ray_direction
            )

            if hit:
                hit_objects.append(hit_object.name)
            else:
                hit_objects.append(None)
                print(f"  Warning: No hit from point {world_point}")

        # Determine the most common hit object (or use first valid hit)
        valid_hits = [h for h in hit_objects if h is not None]
        if valid_hits:
            # Use the most frequent hit object
            hit_name = max(set(valid_hits), key=valid_hits.count)
        else:
            print(f"  Warning: Spline {spline_idx} hit nothing, skipping")
            continue

        # Create the dictionary key
        dict_key = hit_name + "Guide"

        # Store in dictionary (use list of points in world space)
        if dict_key not in named_splines:
            named_splines[dict_key] = []

        # Add all points from this spline (as world coordinates)
        world_points = [curve_obj.matrix_world @ p for p in points]
        named_splines[dict_key].append(world_points)

        # Store original spline data for later separation
        spline_data.append({
            'index': spline_idx,
            'key': dict_key,
            'points': points,
            'world_points': world_points,
            'spline_type': spline.type
        })

        print(f"  Spline {spline_idx} → {dict_key}")

    print(f"\nFound {len(named_splines)} unique guide objects:")
    for key, splines_list in named_splines.items():
        print(f"  • {key}: {len(splines_list)} spline(s)")

    # Now separate each spline into its own curve object
    separated_objects = []

    for key, spline_group in named_splines.items():
        # Create new curve data
        new_curve_data = bpy.data.curves.new(name=key, type='CURVE')
        new_curve_data.dimensions = '3D'

        # Add each spline in this group to the new curve
        for spline_points in spline_group:
            # Create a new spline
            new_spline = new_curve_data.splines.new(type='POLY')

            # Set number of points
            new_spline.points.add(len(spline_points) - 1)

            # Assign point coordinates
            for i, point in enumerate(spline_points):
                # Convert to 4D coordinates (x, y, z, w)
                new_spline.points[i].co = (point.x, point.y, point.z, 1.0)

        # Create new curve object
        new_curve_obj = bpy.data.objects.new(name=key, object_data=new_curve_data)

        # Link to collection
        bpy.context.collection.objects.link(new_curve_obj)
        separated_objects.append(new_curve_obj)

        print(f"Created: {key}")

    # Optional: Delete or hide the original curve
    original_name = curve_obj.name
    curve_obj.hide_set(True)
    print(f"\nOriginal curve '{original_name}' has been hidden")

    # Select all new objects
    bpy.ops.object.select_all(action='DESELECT')
    for obj in separated_objects:
        obj.select_set(True)

    if separated_objects:
        bpy.context.view_layer.objects.active = separated_objects[0]
        print(f"\n✓ Success! Created {len(separated_objects)} new curve objects")
        print("  (Original curve has been hidden - unhide it if needed)")

    return named_splines, separated_objects


# Run the function
if __name__ == "__main__":
    named_splines, new_objects = raycast_down_from_spline()

    # Print summary
    if named_splines:
        print("\n" + "=" * 50)
        print("DICTIONARY CONTENTS:")
        print("=" * 50)
        for key, splines in named_splines.items():
            print(f"\n{key}:")

            spline_ob = bpy.data.objects.get(key)
            key_ob = bpy.data.objects.get(key.replace('Guide', ''))

            if spline_ob and key_ob:
                # Store original transforms before parenting
                original_location = spline_ob.location.copy()
                original_rotation = spline_ob.rotation_euler.copy()
                original_scale = spline_ob.scale.copy()

                # Parent with keep_transform (inverse matrix handling)
                spline_ob.parent = key_ob

                # Method 1: Keep world transform using inverse matrix
                # This calculates the inverse of the parent's matrix and applies it
                spline_ob.matrix_parent_inverse = key_ob.matrix_world.inverted()

                print(f"  • Parented {key} to {key_ob.name}")
                print(f"    - Parent inverse matrix set to maintain world position")

                # Create empty at first point (in world space)
                emp = bpy.data.objects.new(name=key + 'Emp', object_data=None)
                bpy.context.collection.objects.link(emp)

                # Get first point from the spline group (world space)
                point = splines[0][0]

                # Position empty in world space
                # emp.location = Vector((point.x, point.y, point.z))
                emp.empty_display_size = 0.02

                bpy.ops.object.select_all(action='DESELECT')
                emp.select_set(True)
                bpy.context.view_layer.objects.active = emp
                fp_const = emp.constraints.new(type='FOLLOW_PATH')
                fp_const.target = spline_ob
                bpy.ops.constraint.followpath_path_animate(constraint=fp_const.name, owner='OBJECT')
                for fc in spline_ob.data.animation_data.action.fcurves[:]:
                    if fc.data_path == 'eval_time':
                        spline_ob.data.animation_data.action.fcurves.remove(fc)
                        break

                # Optional: Parent empty to the same object with proper inverse
                # emp.parent = key_ob
                # emp.matrix_parent_inverse = key_ob.matrix_world.inverted()

                print(f"  • Created empty {emp.name} at position: ({point.x:.3f}, {point.y:.3f}, {point.z:.3f})")

            else:
                if not spline_ob:
                    print(f"  ✗ Warning: Could not find object '{key}'")
                if not key_ob:
                    print(f"  ✗ Warning: Could not find object '{key.replace('Guide', '')}'")

        # Additional matrix inversion examples and utilities
        print("\n" + "=" * 50)
        print("MATRIX INVERSION NOTES:")
        print("=" * 50)
        print("""
        To convert between local and world space after parenting:

        # World to Local (using parent inverse)
        local_pos = parent.matrix_world.inverted() @ world_pos

        # Local to World
        world_pos = parent.matrix_world @ local_pos

        # Get object's world matrix (includes parent transforms)
        world_matrix = obj.matrix_world

        # Get object's local matrix relative to parent
        local_matrix = obj.matrix_local

        # Manually set parent inverse to keep world position
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
        """)

        # Example: Convert all spline points to local space of their parent
        print("\n" + "=" * 50)
        print("CONVERTING SPLINE POINTS TO LOCAL SPACE:")
        print("=" * 50)

        for key, splines in named_splines.items():
            spline_ob = bpy.data.objects.get(key)
            key_ob = bpy.data.objects.get(key.replace('Guide', ''))

            if spline_ob and key_ob:
                print(f"\n{key}:")
                # Get the inverse matrix of the parent
                inverse_parent_matrix = key_ob.matrix_world.inverted()

                for i, spline_points in enumerate(splines):
                    # Convert first point from world to local space
                    world_point = spline_points[0]
                    local_point = inverse_parent_matrix @ world_point
                    print(f"  Spline {i} first point:")
                    print(f"    World: ({world_point.x:.3f}, {world_point.y:.3f}, {world_point.z:.3f})")
                    print(f"    Local: ({local_point.x:.3f}, {local_point.y:.3f}, {local_point.z:.3f})")