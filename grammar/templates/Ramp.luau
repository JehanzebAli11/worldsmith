
do -- Ramp: wedge rising towards its rises_towards axis (not allowed in F1 patches)
    local p = ws_part("WedgePart", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, {{num:yaw}}, Color3.fromRGB(163, 162, 165), 0, true)
    ws_tag(p, {{id:id}}, "Ramp")
end
