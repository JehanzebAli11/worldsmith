
-- Swap: the new tree is complete. Remove every old Workspace.Worldsmith, then parent the new one.
for _, old in ipairs(workspace:GetChildren()) do
    if old.Name == "Worldsmith" then
        old:Destroy()
    end
end
root.Parent = workspace
print("WS_BUILD_OK|" .. WS_HASH .. "|" .. tostring(#root:GetDescendants()))
