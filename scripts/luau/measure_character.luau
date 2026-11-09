-- Character measurement (WS-04 re-measure after LoadCharacterAppearance = false; same method as
-- WS-03): one play session via run_script_in_play_mode, server-side reads on the Baseplate,
-- away from Workspace.Worldsmith. Teleports are allowed here (this is not the P0 controller).
local Players = game:GetService("Players")
local HttpService = game:GetService("HttpService")
local StarterPlayer = game:GetService("StarterPlayer")
local deadline = os.clock() + 40
local player = Players:GetPlayers()[1]
while not player and os.clock() < deadline do
    task.wait(0.1)
    player = Players:GetPlayers()[1]
end
if not player then
    return "WS_MEASURE|error|no player"
end
local base = workspace:FindFirstChild("SpawnLocation")
if base then
    player.RespawnLocation = base
end
player:LoadCharacter()
local char = player.Character
local hum = char:WaitForChild("Humanoid", 10)
local hrp = char:WaitForChild("HumanoidRootPart", 10)
task.wait(2)
local function v3(v)
    return { v.X, v.Y, v.Z }
end
local out = {}
out.load_character_appearance = StarterPlayer.LoadCharacterAppearance
out.character_auto_loads = Players.CharacterAutoLoads
out.rig = hum.RigType.Name
out.hrp_size = v3(hrp.Size)
out.hip_height = hum.HipHeight
local _, bb = char:GetBoundingBox()
out.bbox = v3(bb)
out.walk_speed = hum.WalkSpeed
out.use_jump_power = hum.UseJumpPower
out.jump_power = hum.JumpPower
out.jump_height = hum.JumpHeight
out.max_slope = hum.MaxSlopeAngle
out.gravity = workspace.Gravity
out.hrp_owner_is_player = hrp:GetNetworkOwner() == player
out.spawned_at = v3(hrp.Position)
out.floor = hum.FloorMaterial.Name
out.hrp_y_standing = hrp.Position.Y
local parts, accessories, clothing = 0, 0, 0
for _, d in ipairs(char:GetDescendants()) do
    if d:IsA("BasePart") then
        parts += 1
    elseif d:IsA("Accessory") then
        accessories += 1
    elseif d:IsA("Clothing") or d:IsA("ShirtGraphic") then
        clothing += 1
    end
end
out.base_parts = parts
out.accessories = accessories
out.clothing = clothing
local scales = {}
for _, n in ipairs({ "BodyHeightScale", "BodyWidthScale", "BodyDepthScale", "HeadScale", "BodyTypeScale", "BodyProportionScale" }) do
    local v = hum:FindFirstChild(n)
    scales[n] = v and v.Value or "none"
end
out.scales = scales
-- 60-stud straight walk (server MoveTo) along +X.
local start = hrp.Position
local target = start + Vector3.new(60, 0, 0)
local finished, reached = nil, nil
local c = hum.MoveToFinished:Connect(function(r)
    finished = os.clock()
    reached = r
end)
local tcmd = os.clock()
hum:MoveTo(target)
local tmove, t60 = nil, nil
while os.clock() - tcmd < 10 and not finished do
    local d = (hrp.Position - start) * Vector3.new(1, 0, 1)
    if not tmove and d.Magnitude > 0.05 then
        tmove = os.clock()
    end
    if not t60 and d.Magnitude >= 59.5 then
        t60 = os.clock()
    end
    task.wait()
end
c:Disconnect()
out.walk60 = {
    reached = reached,
    latency_cmd_to_move = tmove and (tmove - tcmd) or "none",
    move_to_59_5 = (tmove and t60) and (t60 - tmove) or "none",
    cmd_to_finished = finished and (finished - tcmd) or "none",
    end_pos = v3(hrp.Position),
}
-- One running jump (server Humanoid.Jump) while walking +X.
task.wait(0.5)
local s2 = hrp.Position
hum:MoveTo(s2 + Vector3.new(40, 0, 0))
task.wait(0.8)
local vel = hrp.AssemblyLinearVelocity
local yg = hrp.Position.Y
hum.Jump = true
local tj = os.clock()
local takeoff, tto, landing, tland, apex = nil, nil, nil, nil, yg
while os.clock() - tj < 3 do
    local p = hrp.Position
    if not takeoff and p.Y > yg + 0.05 then
        takeoff, tto = p, os.clock()
    end
    if takeoff then
        apex = math.max(apex, p.Y)
        if p.Y <= yg + 0.05 and os.clock() - tto > 0.1 then
            landing, tland = p, os.clock()
            break
        end
    end
    task.wait()
end
out.jump = {
    method = "server Humanoid.Jump",
    vel_before = v3(vel),
    y_ground = yg,
    takeoff = takeoff and v3(takeoff) or "none",
    landing = landing and v3(landing) or "none",
    apex_rise = apex - yg,
    air_time = (tto and tland) and (tland - tto) or "none",
    horizontal = (takeoff and landing) and ((landing - takeoff) * Vector3.new(1, 0, 1)).Magnitude or "none",
}
local s = HttpService:JSONEncode(out)
print("WS_MEASURE|" .. s)
return s
