import Cairo from 'cairo';

const TAU = 2 * Math.PI;
const CX = 64, CY = 61;

function hex(color) {
    return [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16) / 255);
}

function roundedRect(cr, x, y, w, h, r) {
    cr.newSubPath();
    cr.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
    cr.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
    cr.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
    cr.arc(x + r, y + r, r, Math.PI, 1.5 * Math.PI);
    cr.closePath();
}

// Numerals are rendered into an alpha-only (A8) surface and used as a white stencil. The system's fontconfig
// enables RGB subpixel smoothing, which gives text colour fringes on any colour surface; A8 can't hold those.
const numeralCache = new Map();

function numeralsSurface(size) {
    const pixels = Math.max(1, Math.round(size));
    if (!numeralCache.has(pixels)) {
        const surface = new Cairo.ImageSurface(Cairo.Format.A8, pixels, pixels);
        const ctx = new Cairo.Context(surface);
        ctx.scale(pixels / 128, pixels / 128);
        ctx.selectFontFace('Ubuntu Sans', Cairo.FontSlant.NORMAL, Cairo.FontWeight.BOLD);
        ctx.setFontSize(11.5);
        ctx.setSourceRGB(1, 1, 1);
        for (const [text, angle] of [['12', 0], ['3', 90], ['6', 180], ['9', 270]]) {
            const a = angle * Math.PI / 180;
            const x = CX + 37.5 * Math.sin(a), y = CY - 37.5 * Math.cos(a);
            const ext = ctx.textExtents(text);
            ctx.moveTo(x - ext.width / 2 - ext.xBearing, y - ext.height / 2 - ext.yBearing);
            ctx.showText(text);
        }
        ctx.$dispose();
        numeralCache.set(pixels, surface);
    }
    return numeralCache.get(pixels);
}

// Draws the Planet Time app icon (same design as io.github.pavistern.PlanetTime.svg, a 128x128 grid):
// a dark rounded-square clock with hands set to `now` (a GLib.DateTime).
export function drawClockIcon(cr, size, now) {
    const scale = size / 128;
    // Keep thin strokes at least ~1 physical pixel wide on small icons.
    const minWidth = width => Math.max(width, 1.0 / scale);

    cr.save();
    cr.scale(scale, scale);
    cr.setLineCap(Cairo.LineCap.ROUND);

    // Soft shadow, depth lip, body, top highlight, darker hub
    roundedRect(cr, 9, 12, 110, 110, 31);
    cr.setSourceRGBA(0, 0, 0, 0.08);
    cr.fill();
    roundedRect(cr, 10, 11, 108, 109, 29);
    cr.setSourceRGBA(0, 0, 0, 0.14);
    cr.fill();
    roundedRect(cr, 10, 10, 108, 108, 28);
    cr.setSourceRGB(...hex('#161517'));
    cr.fill();

    const body = new Cairo.LinearGradient(0, 7, 0, 115);
    body.addColorStopRGB(0, ...hex('#3d3c3e'));
    body.addColorStopRGB(1, ...hex('#232224'));
    roundedRect(cr, 10, 7, 108, 108, 28);
    cr.setSource(body);
    cr.fill();

    const rim = new Cairo.LinearGradient(0, 7, 0, 115);
    rim.addColorStopRGBA(0, 1, 1, 1, 0.18);
    rim.addColorStopRGBA(0.35, 1, 1, 1, 0);
    roundedRect(cr, 10.5, 7.5, 107, 107, 27.5);
    cr.setSource(rim);
    cr.setLineWidth(1);
    cr.stroke();
    // Faint outline so the dark body keeps its shape on a dark dock
    roundedRect(cr, 10.5, 7.5, 107, 110.5, 27.5);
    cr.setSourceRGBA(1, 1, 1, 0.07);
    cr.stroke();

    const hub = new Cairo.RadialGradient(CX, CY, 0, CX, CY, 30);
    hub.addColorStopRGBA(0, ...hex('#121213'), 0.6);
    hub.addColorStopRGBA(1, ...hex('#121213'), 0);
    cr.arc(CX, CY, 30, 0, TAU);
    cr.setSource(hub);
    cr.fill();

    const line = (angle, from, to) => {
        const x = Math.sin(angle), y = -Math.cos(angle);
        cr.moveTo(CX + from * x, CY + from * y);
        cr.lineTo(CX + to * x, CY + to * y);
    };

    // Minute ticks, then hour ticks (12, 3, 6 and 9 get numerals instead)
    cr.setSourceRGBA(1, 1, 1, 0.45);
    cr.setLineWidth(minWidth(0.9));
    for (let i = 0; i < 60; i++) {
        if (i % 5 !== 0)
            line(i * TAU / 60, 43.2, 45.5);
    }
    cr.stroke();
    cr.setSourceRGBA(1, 1, 1, 0.95);
    cr.setLineWidth(minWidth(2.0));
    for (let i = 0; i < 12; i++) {
        if (i % 3 !== 0)
            line(i * TAU / 12, 40.5, 45.5);
    }
    cr.stroke();

    // Numerals (pre-rendered at this size, painted 1:1 in device pixels)
    cr.save();
    cr.scale(1 / scale, 1 / scale);
    cr.setSourceRGB(1, 1, 1);
    cr.maskSurface(numeralsSurface(size), 0, 0);
    cr.restore();

    // Hands, each with a soft shadow underneath
    const seconds = now.get_second();
    const minutes = now.get_minute() + seconds / 60;
    const hours = (now.get_hour() % 12) + minutes / 60;
    const hand = (angle, tail, length, width, color, alpha, dx = 0, dy = 0) => {
        cr.save();
        cr.translate(dx, dy);
        cr.setSourceRGBA(...hex(color), alpha);
        cr.setLineWidth(minWidth(width));
        line(angle, -tail, length);
        cr.stroke();
        cr.restore();
    };
    for (const [dx, dy, color, alpha] of [[0.9, 1.8, '#000000', 0.35], [0, 0, '#f4f1ec', 1]]) {
        hand(hours * TAU / 12, 3, 21, 6.2, color, alpha, dx, dy);
        hand(minutes * TAU / 60, 3, 33, 4.6, color, alpha, dx, dy);
    }
    hand(seconds * TAU / 60, 10, 40, 1.6, '#000000', 0.3, 0.8, 1.6);
    hand(seconds * TAU / 60, 10, 40, 1.6, '#e8452c', 1);

    cr.arc(CX, CY, 5.4, 0, TAU);
    cr.setSourceRGB(...hex('#f4f1ec'));
    cr.fill();
    cr.arc(CX, CY, 3.8, 0, TAU);
    cr.setSourceRGB(...hex('#e8452c'));
    cr.fill();

    cr.restore();
}
