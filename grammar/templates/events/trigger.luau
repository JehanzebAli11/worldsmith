-- Worldsmith trigger (fixed template): prints its ws_event once per character that enters it.
local zone = script.Parent
local lib = require(zone:FindFirstAncestor("Worldsmith"):WaitForChild("ws_lib"))
local event = zone:GetAttribute("ws_event")
local seen = setmetatable({}, { __mode = "k" })
zone.Touched:Connect(function(hit)
    local character = lib.characterOf(hit)
    if character and not seen[character] then
        seen[character] = true
        lib.emit(event, zone.Name, character)
    end
end)
