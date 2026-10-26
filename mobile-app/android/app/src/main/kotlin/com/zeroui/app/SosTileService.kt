package com.zeroui.app

import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * Zero UI SOS Quick Settings tile
 * ===============================
 * One-tap SOS fallback that works even when the SOS accessibility gesture is
 * not enabled. Pulling down the Quick Settings shade and tapping "Zero UI SOS"
 * hands the trigger into the same shared emergency state machine as the fall
 * detector and the hardware-button gesture.
 *
 * Design notes:
 *  * Tiles can only be tapped by an intentional user action on a visible
 *    shade, so this is not a background surveillance mechanism — just a fast,
 *    discoverable button.
 *  * The tile always starts the SAME shared lifecycle (countdown → PIN →
 *    backend), never a shortcut emergency without verification.
 *  * The tile is a fallback, not a replacement for the documented
 *    triple-volume-down gesture.
 */
class SosTileService : TileService() {

    override fun onStartListening() {
        super.onStartListening()
        updateTile()
    }

    override fun onTileAdded() {
        super.onTileAdded()
        updateTile()
    }

    override fun onClick() {
        super.onClick()
        // Same hand-off path as the accessibility gesture: the app process
        // receives the broadcast and runs startCountdown(triggerType: 'manual',
        // metadata: trigger_source = 'quick_settings_tile').
        val intent = android.content.Intent(SosAccessibilityService.ACTION_SOS_TRIGGERED).apply {
            `package` = packageName
            putExtra(SosAccessibilityService.EXTRA_TRIGGER_SOURCE, "quick_settings_tile")
        }
        sendBroadcast(intent)
        // Briefly mark the tile as unavailable so a double-tap cannot race.
        qsTile?.let { tile ->
            tile.state = Tile.STATE_UNAVAILABLE
            tile.updateTile()
        }
    }

    private fun updateTile() {
        qsTile?.let { tile ->
            tile.state = Tile.STATE_ACTIVE
            tile.updateTile()
        }
    }

    override fun onTileRemoved() = super.onTileRemoved()
}
