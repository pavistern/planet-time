import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as AppDisplay from 'resource:///org/gnome/shell/ui/appDisplay.js';
import * as IconGrid from 'resource:///org/gnome/shell/ui/iconGrid.js';

import {drawClockIcon} from './clockFace.js';

const LIVE_APP_IDS = new Set(['local.LiveClock.desktop']);

const LiveClockIcon = GObject.registerClass(
class LiveClockIcon extends St.DrawingArea {
    _init(iconSize) {
        const {scaleFactor} = St.ThemeContext.get_for_stage(global.stage);
        super._init({
            width: iconSize * scaleFactor,
            height: iconSize * scaleFactor,
        });
    }

    vfunc_repaint() {
        const cr = this.get_context();
        const [width, height] = this.get_surface_size();
        drawClockIcon(cr, Math.min(width, height), GLib.DateTime.new_now_local());
        cr.$dispose();
    }
});

export default class LiveClockIconExtension extends Extension {
    enable() {
        const icons = new Set();
        const originalCreateIconTexture = Shell.App.prototype.create_icon_texture;

        // Every app icon in the shell (dock, app grid, Alt+Tab, search) is created through
        // ShellApp.create_icon_texture(), so swapping it covers all of them.
        Shell.App.prototype.create_icon_texture = function (iconSize) {
            if (!LIVE_APP_IDS.has(this.get_id()))
                return originalCreateIconTexture.call(this, iconSize);

            const icon = new LiveClockIcon(iconSize);
            icons.add(icon);
            icon.connect('destroy', () => icons.delete(icon));
            return icon;
        };

        this._icons = icons;
        this._originalCreateIconTexture = originalCreateIconTexture;
        this._scheduleTick();
        this._recreateAppIcons();
    }

    disable() {
        Shell.App.prototype.create_icon_texture = this._originalCreateIconTexture;
        this._originalCreateIconTexture = null;

        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }

        this._recreateAppIcons();
        this._icons = null;
    }

    _scheduleTick() {
        // Fire just after the next whole second so the hands move in step with the top bar clock.
        const now = GLib.DateTime.new_now_local();
        const delayMs = 1000 - Math.floor(now.get_microsecond() / 1000) + 2;

        this._tickId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delayMs, () => {
            for (const icon of this._icons) {
                if (icon.mapped)
                    icon.queue_repaint();
            }
            this._scheduleTick();
            return GLib.SOURCE_REMOVE;
        });
    }

    _recreateAppIcons() {
        // Icons built before the extension started (the app grid, folders, the dock) are plain St.Icons,
        // which ignore 'icon-theme-changed'. Rebuild every Planet Time icon and all folder previews directly.
        const visit = actor => {
            const isOurApp = actor.icon instanceof IconGrid.BaseIcon && LIVE_APP_IDS.has(actor.app?.get_id?.());
            if (isOurApp || actor instanceof AppDisplay.FolderIcon)
                actor.icon.update();
            for (const child of actor.get_children())
                visit(child);
        };
        visit(global.stage);
        // Anything else that draws app icons and listens for theme changes refreshes here.
        St.TextureCache.get_default().emit('icon-theme-changed');
    }
}
