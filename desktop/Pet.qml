import QtQuick

Item {
    id: pet
    property string mood: "IDLE"
    property bool reducedMotion: false
    property bool onDesktop: false
    property real level: 0
    property bool blink: false
    property bool sleeping: false
    property int motionStep: 0
    readonly property bool busy: mood === "THINKING" || mood === "EXECUTING" || mood === "VERIFYING"

    Timer {
        interval: pet.mood === "SPEAKING" ? 140 : 170
        running: pet.onDesktop && !pet.reducedMotion &&
            ((!pet.sleeping && pet.mood === "IDLE") || pet.busy || pet.mood === "SPEAKING")
        repeat: true
        onTriggered: pet.motionStep = (pet.motionStep + 1) % 24
        onRunningChanged: if (!running) pet.motionStep = 0
    }

    Timer {
        interval: 120000
        running: pet.onDesktop && !pet.reducedMotion && pet.mood === "IDLE"
        onTriggered: pet.sleeping = true
    }
    onMoodChanged: { sleeping = false; motionStep = 0 }
    onOnDesktopChanged: sleeping = false

    Timer {
        interval: 4900
        running: pet.onDesktop && !pet.reducedMotion && !pet.sleeping && pet.mood === "IDLE"
        repeat: true
        onTriggered: {
            pet.blink = true
            reopen.restart()
        }
        onRunningChanged: if (!running) pet.blink = false
    }

    Timer {
        id: reopen
        interval: 125
        onTriggered: pet.blink = false
    }

    Canvas {
        id: robot
        anchors.fill: parent
        scale: pet.mood === "IDLE" ? 1 + 0.012 * Math.sin(pet.motionStep * Math.PI / 12) : 1
        renderTarget: Canvas.Image
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        Connections {
            target: pet
            function onMoodChanged() { robot.requestPaint() }
            function onBlinkChanged() { robot.requestPaint() }
            function onSleepingChanged() { robot.requestPaint() }
            function onLevelChanged() { if (pet.mood === "LISTENING" && !pet.reducedMotion) robot.requestPaint() }
            function onMotionStepChanged() { if (pet.busy || pet.mood === "SPEAKING") robot.requestPaint() }
        }

        onPaint: {
            var c = getContext("2d")
            c.reset()
            var scale = Math.min(width, height) / 96
            c.translate((width - scale * 96) / 2, (height - scale * 96) / 2)
            c.scale(scale, scale)

            function oval(x, y, w, h, paint) {
                c.beginPath()
                c.ellipse(x, y, w, h)
                c.fillStyle = paint
                c.fill()
            }
            function gradient(top, bottom, high, low) {
                var g = c.createLinearGradient(0, top, 0, bottom)
                g.addColorStop(0, high)
                g.addColorStop(1, low)
                return g
            }
            oval(17, 83, 62, 10, "#38000000")
            oval(25, 55, 46, 34, gradient(54, 90, "#eee4ce", "#8c8067"))
            oval(19, 68, 18, 18, "#a79a80")
            oval(59, 68, 18, 18, "#a79a80")
            oval(22, 74, 20, 17, gradient(72, 92, "#e3d6ba", "#aa997b"))
            oval(54, 74, 20, 17, gradient(72, 92, "#e3d6ba", "#aa997b"))
            oval(39, 68, 18, 15, "#302e25")
            c.fillStyle = "#d7e58f"
            c.beginPath()
            c.moveTo(48, 70); c.lineTo(42, 80); c.lineTo(48, 77); c.lineTo(54, 80)
            c.closePath(); c.fill()

            oval(5, 27, 15, 27, "#acac78")
            oval(76, 27, 15, 27, "#acac78")
            oval(8, 7, 80, 58, gradient(7, 65, "#fff3dd", "#b3a385"))
            oval(14, 13, 68, 47, "#746b57")
            oval(16, 15, 64, 43, gradient(15, 58, "#2d2c22", "#141611"))

            var flat = pet.blink || pet.sleeping || pet.mood === "MUTED" || pet.mood === "OFFLINE"
            var open = pet.mood === "LISTENING" || pet.mood === "THINKING" ||
                pet.mood === "EXECUTING" || pet.mood === "VERIFYING" || pet.mood === "CLARIFYING"
            var attention = pet.mood === "LISTENING" && !pet.reducedMotion ? pet.level : 0
            var glance = pet.busy ? 2.5 * Math.sin(pet.motionStep * Math.PI / 12) : 0
            c.strokeStyle = pet.mood === "ERROR" ? "#e9a98a" : flat ? "#a7a889" : "#e4eea2"
            c.fillStyle = c.strokeStyle
            c.lineWidth = 2.8
            c.lineCap = "round"
            for (var x of [36, 60]) {
                if (open) {
                    oval(x - 2.8 + glance, 32 - attention * 2, 5.6, 8 + attention * 4, c.strokeStyle)
                } else {
                    c.beginPath()
                    c.moveTo(x - 5, 38)
                    if (flat) c.lineTo(x + 5, 38)
                    else c.quadraticCurveTo(x, pet.mood === "ERROR" ? 44 : pet.mood === "SUCCESS" ? 23 : 27, x + 5, 38)
                    c.stroke()
                }
            }
            if (pet.mood === "SPEAKING")
                oval(44, 43, 8, 3.5 + 2 * Math.abs(Math.sin(pet.motionStep * Math.PI / 4)), "#d7e58f")
            else if (pet.mood === "SUCCESS" || pet.mood === "REMINDER")
                oval(43, 43, 10, 4, "#d7e58f")
            if (pet.mood === "MUTED") {
                oval(69, 46, 15, 15, "#302e25")
                c.beginPath(); c.moveTo(72, 49); c.lineTo(80, 57); c.stroke()
            }
        }
    }
}
