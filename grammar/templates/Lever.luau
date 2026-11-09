
do -- Lever: base, handle with a ProximityPrompt, and a touch zone; activating it latches on
    local m = Instance.new("Model")
    m.Name = {{id:id}}
    ws_tag(m, {{id:id}}, "Lever")
    m:SetAttribute("ws_gate", {{id:gate}})
    m:SetAttribute("ws_active", false)
    ws_part("Part", "base", m, {{num:base_sx}}, {{num:base_sy}}, {{num:base_sz}}, {{num:base_px}}, {{num:base_py}}, {{num:base_pz}}, 0, Color3.fromRGB(99, 95, 98), 0, true)
    local handle = ws_part("Part", "handle", m, {{num:handle_sx}}, {{num:handle_sy}}, {{num:handle_sz}}, {{num:handle_px}}, {{num:handle_py}}, {{num:handle_pz}}, 0, Color3.fromRGB(196, 40, 28), 0, true)
    local zone = ws_part("Part", "zone", m, {{num:zone_sx}}, {{num:zone_sy}}, {{num:zone_sz}}, {{num:zone_px}}, {{num:zone_py}}, {{num:zone_pz}}, 0, Color3.fromRGB(255, 255, 0), 0.9, false)
    zone.CanQuery = false
    zone.CastShadow = false
    local prompt = Instance.new("ProximityPrompt")
    prompt.Name = "prompt"
    prompt.ActionText = "Pull"
    prompt.ObjectText = "Lever"
    prompt.HoldDuration = 0
    prompt.MaxActivationDistance = 8
    prompt.RequiresLineOfSight = false
    prompt.Parent = handle
    ws_events(m, "lever")
    m.Parent = root
end
