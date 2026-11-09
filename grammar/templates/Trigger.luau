
do -- Trigger: event volume (no collision, ignored by raycasts); prints its ws_event on touch
    local p = ws_part("Part", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, Color3.fromRGB(0, 255, 255), 0.85, false)
    p.CanQuery = false
    p.CastShadow = false
    ws_tag(p, {{id:id}}, "Trigger")
    p:SetAttribute("ws_event", {{enum:event}})
    p:SetAttribute("ws_region", {{enum:region}})
    p:SetAttribute("ws_host", {{id:host}})
    ws_events(p, "trigger")
end
