
do -- Goal: pad on its platform's top centre (GOAL_TOUCHED comes from its trigger)
    local p = ws_part("Part", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, Color3.fromRGB(75, 151, 75), 0, true)
    ws_tag(p, {{id:id}}, "Goal")
end
