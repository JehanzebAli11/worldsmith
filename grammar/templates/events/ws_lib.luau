-- Worldsmith event library (fixed template). One line per event:
-- WS|<nonce>|<EVENT>|id=<id>|pos=<x,y,z or na>|t=<server time>
local lib = {}
local root = script.Parent
local EVENTS = { LEVER_ACTIVATED = true, GATE_OPENED = true, BRIDGE_CROSSED = true, KILL_FLOOR_TOUCHED = true, GOAL_TOUCHED = true }

function lib.characterOf(hit)
    local model = hit and hit:FindFirstAncestorOfClass("Model")
    while model do
        local hum = model:FindFirstChildOfClass("Humanoid")
        if hum then
            if hum.Health > 0 then
                return model, hum
            end
            return nil
        end
        model = model:FindFirstAncestorOfClass("Model")
    end
    return nil
end

local function nonce()
    local n = root:GetAttribute("ws_nonce")
    if type(n) == "string" and #n >= 8 and #n <= 32 and string.match(n, "^[0-9a-f]+$") then
        return n
    end
    return "-"
end

function lib.emit(event, id, character)
    if not EVENTS[event] then
        return
    end
    local pos = "na"
    local hrp = character and character:FindFirstChild("HumanoidRootPart")
    if hrp then
        local p = hrp.Position - (root:GetAttribute("ws_origin") or Vector3.zero)
        pos = string.format("%.2f,%.2f,%.2f", p.X, p.Y, p.Z)
    end
    print(string.format("WS|%s|%s|id=%s|pos=%s|t=%.3f", nonce(), event, id, pos, workspace:GetServerTimeNow()))
end

return lib
