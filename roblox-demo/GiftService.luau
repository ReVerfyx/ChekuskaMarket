-- ModuleScript in ServerScriptService of an experience YOU own.
-- Example inventory: player.Inventory.Raccoon (IntValue). Player data persistence
-- is supplied by the experience; this module only moves one item while both
-- players are online and returns a receipt after checking counts.
local Players = game:GetService("Players")
local HttpService = game:GetService("HttpService")

local GiftService = {}
local processed = {}
local ALLOWED_ITEMS = { Raccoon = true }

function GiftService.Gift(sellerUserId, recipientUserId, itemName, orderId)
    if type(sellerUserId) ~= "number" or type(recipientUserId) ~= "number"
        or type(orderId) ~= "string" or #orderId > 80 or #orderId < 1
        or not ALLOWED_ITEMS[itemName] then
        return false, "invalid_request"
    end
    if processed[orderId] then
        return false, "already_processed"
    end
    local seller = Players:GetPlayerByUserId(sellerUserId)
    local recipient = Players:GetPlayerByUserId(recipientUserId)
    if not seller or not recipient or seller == recipient then
        return false, "both_players_must_be_online"
    end
    local sellerInventory = seller:FindFirstChild("Inventory")
    local recipientInventory = recipient:FindFirstChild("Inventory")
    if not sellerInventory or not recipientInventory then
        return false, "inventory_missing"
    end
    local from = sellerInventory:FindFirstChild(itemName)
    local to = recipientInventory:FindFirstChild(itemName)
    if not from or not to or not from:IsA("IntValue") or not to:IsA("IntValue")
        or from.Value < 1 then
        return false, "out_of_stock"
    end
    -- Do not yield between these operations. The game must persist both
    -- inventories and handle transaction recovery before real-money sales.
    from.Value -= 1
    to.Value += 1
    processed[orderId] = true
    local receipt = HttpService:GenerateGUID(false)
    return true, { receipt = receipt, recipientUserId = recipient.UserId,
        item = itemName, senderRemaining = from.Value, recipientCount = to.Value }
end

return GiftService
