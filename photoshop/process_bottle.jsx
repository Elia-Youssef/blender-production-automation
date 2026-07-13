#target photoshop
/*
  process_bottle.jsx
  ------------------
  Take ONE transparent PNG, resize the visible layer so its longest edge is
  3600 px, center it on the 4000x4000 canvas, save the PNG in place, then add
  a white background and save a JPG copy to the chosen output folder.

  run_folder.ps1 injects the PNG file path and JPG output folder below.
*/
(function () {

    // ---------------- config ----------------
    var TARGET_LONGEST = 3600;
    var EXPECTED_CANVAS = 4000;
    var JPEG_QUALITY = 10; // Photoshop range: 0-12
    // ----------------------------------------

    var pngPlaceholder = "TARGET_PNG_" + "PLACEHOLDER";
    var jpgFolderPlaceholder = "TARGET_JPG_FOLDER_" + "PLACEHOLDER";

    var TARGET_PNG = "TARGET_PNG_PLACEHOLDER";
    var TARGET_JPG_FOLDER = "TARGET_JPG_FOLDER_PLACEHOLDER";

    function withoutExtension(name) {
        return name.replace(/\.[^\.]+$/, "");
    }

    function joinPath(folder, fileName) {
        return folder.fsName.replace(/[\\\/]$/, "") + "/" + fileName;
    }

    function px(value) {
        return value.value;
    }

    var pngFile;
    var jpgFolder;

    if (TARGET_PNG === pngPlaceholder) {
        pngFile = File.openDialog("Select a transparent PNG to process", "*.png");
        if (!pngFile) { return; }
    } else {
        pngFile = File(TARGET_PNG);
    }

    if (TARGET_JPG_FOLDER === jpgFolderPlaceholder) {
        jpgFolder = Folder.selectDialog("Select the JPG output folder");
        if (!jpgFolder) { return; }
    } else {
        jpgFolder = Folder(TARGET_JPG_FOLDER);
    }

    if (!pngFile.exists) { throw new Error("PNG not found: " + pngFile.fsName); }
    if (!jpgFolder.exists && !jpgFolder.create()) {
        throw new Error("Could not create JPG output folder: " + jpgFolder.fsName);
    }

    var oldUnits = app.preferences.rulerUnits;
    var oldDialogs = app.displayDialogs;
    app.preferences.rulerUnits = Units.PIXELS;
    app.displayDialogs = DialogModes.NO;

    var doc = null;
    try {
        doc = app.open(pngFile);

        if (Math.round(px(doc.width)) !== EXPECTED_CANVAS ||
            Math.round(px(doc.height)) !== EXPECTED_CANVAS) {
            throw new Error("Expected a 4000x4000 canvas, got " +
                Math.round(px(doc.width)) + "x" + Math.round(px(doc.height)) + ".");
        }

        var layer = doc.activeLayer;
        if (layer.isBackgroundLayer) {
            try { layer.isBackgroundLayer = false; } catch (e) {}
        }

        var b = layer.bounds;
        var w = px(b[2]) - px(b[0]);
        var h = px(b[3]) - px(b[1]);
        if (w <= 0 || h <= 0) { throw new Error("Layer has no visible pixels."); }

        var scale = TARGET_LONGEST / Math.max(w, h);
        if (Math.abs(scale - 1) > 1e-6) {
            layer.resize(scale * 100.0, scale * 100.0, AnchorPosition.MIDDLECENTER);
        }

        b = layer.bounds;
        var cx = (px(b[0]) + px(b[2])) / 2.0;
        var cy = (px(b[1]) + px(b[3])) / 2.0;
        var dx = Math.round(px(doc.width) / 2.0 - cx);
        var dy = Math.round(px(doc.height) / 2.0 - cy);
        if (dx !== 0 || dy !== 0) { layer.translate(dx, dy); }

        // Save the adjusted transparent PNG back to its original file.
        doc.save();

        var white = new SolidColor();
        white.rgb.red = 255;
        white.rgb.green = 255;
        white.rgb.blue = 255;

        var bg = doc.artLayers.add();
        bg.name = "White background";
        bg.move(layer, ElementPlacement.PLACEAFTER);
        doc.activeLayer = bg;
        doc.selection.selectAll();
        doc.selection.fill(white, ColorBlendMode.NORMAL, 100, false);
        doc.selection.deselect();
        doc.activeLayer = layer;

        var jpgName = withoutExtension(pngFile.name) + ".jpg";
        var jpgFile = File(joinPath(jpgFolder, jpgName));
        var jpgOptions = new JPEGSaveOptions();
        jpgOptions.quality = JPEG_QUALITY;
        jpgOptions.embedColorProfile = true;
        jpgOptions.formatOptions = FormatOptions.STANDARDBASELINE;
        jpgOptions.matte = MatteType.WHITE;

        doc.saveAs(jpgFile, jpgOptions, true, Extension.LOWERCASE);
        doc.close(SaveOptions.DONOTSAVECHANGES);
        doc = null;
    } finally {
        if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {} }
        app.preferences.rulerUnits = oldUnits;
        app.displayDialogs = oldDialogs;
    }
})();
