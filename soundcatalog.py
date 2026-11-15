"""Sound set table for the DDNet / TClient audio asset system.

The engine resolves every sound through CDataSoundset entries generated from
datasrc/content.py (src/game/client/components/sounds.cpp loads
assets/audio/<pack>/<filename>). This module mirrors that table so the tool can
address each individual sample of a pack.
"""

from __future__ import annotations

CATEGORIES = ("Weapons", "Player", "Hook", "Pickups", "Feedback", "Chat", "CTF", "Music")

SETS = [
    # name, category, label, description, files
    ("gun_fire", "Weapons", "Pistol fire", "Laser gun shot.", ["wp_gun_fire-01.wv", "wp_gun_fire-02.wv", "wp_gun_fire-03.wv"]),
    ("shotgun_fire", "Weapons", "Shotgun fire", "Shotgun shot.", ["wp_shotty_fire-01.wv", "wp_shotty_fire-02.wv", "wp_shotty_fire-03.wv"]),
    ("grenade_fire", "Weapons", "Grenade launch", "Grenade launcher shot.", ["wp_flump_launch-01.wv", "wp_flump_launch-02.wv", "wp_flump_launch-03.wv"]),
    ("hammer_fire", "Weapons", "Hammer swing", "Hammer swing (no hit).", ["wp_hammer_swing-01.wv", "wp_hammer_swing-02.wv", "wp_hammer_swing-03.wv"]),
    ("hammer_hit", "Weapons", "Hammer hit", "Hammer connecting with something.", ["wp_hammer_hit-01.wv", "wp_hammer_hit-02.wv", "wp_hammer_hit-03.wv"]),
    ("ninja_fire", "Weapons", "Ninja slash", "Ninja sword attack.", ["wp_ninja_attack-01.wv", "wp_ninja_attack-02.wv", "wp_ninja_attack-03.wv"]),
    ("grenade_explode", "Weapons", "Grenade explode", "Grenade detonation.", ["wp_flump_explo-01.wv", "wp_flump_explo-02.wv", "wp_flump_explo-03.wv"]),
    ("ninja_hit", "Weapons", "Ninja hit", "Ninja slash hitting a tee.", ["wp_ninja_hit-01.wv", "wp_ninja_hit-02.wv", "wp_ninja_hit-03.wv"]),
    ("laser_fire", "Weapons", "Laser fire", "Laser rifle shot.", ["wp_laser_fire-01.wv", "wp_laser_fire-02.wv", "wp_laser_fire-03.wv"]),
    ("laser_bounce", "Weapons", "Laser bounce", "Laser rifle ricochet.", ["wp_laser_bnce-01.wv", "wp_laser_bnce-02.wv", "wp_laser_bnce-03.wv"]),
    ("weapon_switch", "Weapons", "Weapon switch", "Switching weapons.", ["wp_switch-01.wv", "wp_switch-02.wv", "wp_switch-03.wv"]),
    ("player_pain_short", "Player", "Pain (short)", "Short hurt grunt.", ["vo_teefault_pain_short-%02d.wv" % i for i in range(1, 13)]),
    ("player_pain_long", "Player", "Pain (long)", "Long hurt grunt.", ["vo_teefault_pain_long-01.wv", "vo_teefault_pain_long-02.wv"]),
    ("body_land", "Player", "Landing", "Tee hitting the ground.", ["foley_land-01.wv", "foley_land-02.wv", "foley_land-03.wv", "foley_land-04.wv"]),
    ("player_airjump", "Player", "Double jump", "Air jump.", ["foley_dbljump-01.wv", "foley_dbljump-02.wv", "foley_dbljump-03.wv"]),
    ("player_jump", "Player", "Footsteps / jump", "8 samples: 4 left foot then 4 right foot.", ["foley_foot_left-%02d.wv" % i for i in range(1, 5)] + ["foley_foot_right-%02d.wv" % i for i in range(1, 5)]),
    ("player_die", "Player", "Death", "Tee dying.", ["foley_body_splat-01.wv", "foley_body_splat-02.wv", "foley_body_splat-03.wv"]),
    ("player_spawn", "Player", "Spawn", "Tee spawning.", ["vo_teefault_spawn-%02d.wv" % i for i in range(1, 8)]),
    ("player_skid", "Player", "Skid", "Braking / skidding.", ["sfx_skid-01.wv", "sfx_skid-02.wv", "sfx_skid-03.wv", "sfx_skid-04.wv"]),
    ("tee_cry", "Player", "Cry", "Tee crying (emote).", ["vo_teefault_cry-01.wv", "vo_teefault_cry-02.wv"]),
    ("hook_loop", "Hook", "Hook loop", "Looping hook reel sound.", ["hook_loop-01.wv", "hook_loop-02.wv"]),
    ("hook_attach_ground", "Hook", "Hook hits ground", "Hook attaching to a wall.", ["hook_attach-01.wv", "hook_attach-02.wv", "hook_attach-03.wv"]),
    ("hook_attach_player", "Hook", "Hook hits tee", "Hook attaching to a player.", ["foley_body_impact-01.wv", "foley_body_impact-02.wv", "foley_body_impact-03.wv"]),
    ("hook_noattach", "Hook", "Hook miss", "Hook not attaching.", ["hook_noattach-01.wv", "hook_noattach-02.wv"]),
    ("pickup_health", "Pickups", "Health pickup", "Picking up health.", ["sfx_pickup_hrt-01.wv", "sfx_pickup_hrt-02.wv"]),
    ("pickup_armor", "Pickups", "Armor pickup", "Picking up armor.", ["sfx_pickup_arm-01.wv", "sfx_pickup_arm-02.wv", "sfx_pickup_arm-03.wv", "sfx_pickup_arm-04.wv"]),
    ("pickup_grenade", "Pickups", "Grenade pickup", "Picking up the grenade launcher.", ["sfx_pickup_launcher.wv"]),
    ("pickup_shotgun", "Pickups", "Shotgun pickup", "Picking up the shotgun.", ["sfx_pickup_sg.wv"]),
    ("pickup_ninja", "Pickups", "Ninja pickup", "Picking up the ninja.", ["sfx_pickup_ninja.wv"]),
    ("weapon_spawn", "Pickups", "Weapon spawn", "Weapon respawning.", ["sfx_spawn_wpn-01.wv", "sfx_spawn_wpn-02.wv", "sfx_spawn_wpn-03.wv"]),
    ("weapon_noammo", "Feedback", "No ammo", "Firing with no ammo.", ["wp_noammo-01.wv", "wp_noammo-02.wv", "wp_noammo-03.wv", "wp_noammo-04.wv", "wp_noammo-05.wv"]),
    ("hit", "Feedback", "Hit indicator", "Weak hit feedback sound.", ["sfx_hit_weak-01.wv", "sfx_hit_weak-02.wv"]),
    ("chat_server", "Chat", "Chat: server", "Server chat message.", ["sfx_msg-server.wv"]),
    ("chat_client", "Chat", "Chat: client", "Client chat message.", ["sfx_msg-client.wv"]),
    ("chat_highlight", "Chat", "Chat: highlight", "Highlighted chat message.", ["sfx_msg-highlight.wv"]),
    ("ctf_drop", "CTF", "Flag drop", "Flag dropped.", ["sfx_ctf_drop.wv"]),
    ("ctf_return", "CTF", "Flag return", "Flag returned to base.", ["sfx_ctf_rtn.wv"]),
    ("ctf_grab_pl", "CTF", "Flag grab (own)", "Your team grabs the flag.", ["sfx_ctf_grab_pl.wv"]),
    ("ctf_grab_en", "CTF", "Flag grab (enemy)", "Enemy team grabs the flag.", ["sfx_ctf_grab_en.wv"]),
    ("ctf_capture", "CTF", "Flag capture", "Flag captured.", ["sfx_ctf_cap_pl.wv"]),
    ("menu", "Music", "Menu music", "Background music in the main menu.", ["music_menu.wv"]),
]

SET_BY_NAME = {name: {"name": name, "category": cat, "label": label, "desc": desc, "files": files}
               for name, cat, label, desc, files in SETS}

ALL_FILES = [f for _n, _c, _l, _d, files in SETS for f in files]
FILE_TO_SET = {}
for _name, _cat, _label, _desc, _files in SETS:
    for _i, _f in enumerate(_files):
        FILE_TO_SET[_f] = (_name, _i)


def variant_label(set_name: str, index: int) -> str:
    if set_name == "player_jump":
        return ("left foot #%d" % (index + 1)) if index < 4 else ("right foot #%d" % (index - 3))
    files = SET_BY_NAME[set_name]["files"]
    if len(files) == 1:
        return "single sample"
    return "variant %d" % (index + 1)


def total_slots() -> int:
    return len(ALL_FILES)
