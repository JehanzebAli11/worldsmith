
do -- KillFloor: prints KILL_FLOOR_TOUCHED and ends the character
    local p = ws_part("Part", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, Color3.fromRGB(196, 40, 28), 0, true)
    ws_tag(p, {{id:id}}, "KillFloor")
    ws_events(p, "kill_floor")
end
