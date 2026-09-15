"""Programmazione da stick, guidata dalla tastiera.

La procedura Spektrum e' fatta di toni e di posizioni del gas, con tre secondi
di tempo per rispondere a un annuncio. Un programma non sente i toni e una
persona non comanda il gas al millisecondo, quindi qui le due cose stanno
insieme: il collegamento e la temporizzazione li tiene il programma, la
decisione di quando muovere il gas la prende chi ascolta.

    python -m smart_esc_tool.stickprog COM5

Tasti:
    B   gas al MINIMO  - entra nel parametro appena annunciato
    F   gas al MASSIMO - conferma l'opzione appena annunciata
    R   riaccende l'ESC a gas pieno (rientra in programmazione)
    Q   esce, gas al minimo e uscita spenta

Toni dei parametri, per l'Avian standard:

     1 breve                        1. Aircraft Type
     2 brevi                        2. Brake Type
     3 brevi                        3. Brake Force
     4 brevi                        4. Voltage Cutoff Type
     1 lungo                        5. Numero di celle
     1 lungo + 1 breve              6. Cutoff Voltage
     1 lungo + 2 brevi              7. BEC Voltage
     1 lungo + 3 brevi              8. Start-Up Mode
     1 lungo + 4 brevi              9. Timing
     2 lunghi                      10. Motor Rotation
     2 lunghi + 1 breve            11. Freewheel Mode
     2 lunghi + 2 brevi            12. FACTORY RESET
     2 lunghi + serie di brevi     13. Exit (salva ed esce)
"""
import sys
import time

try:
    import msvcrt
except ImportError:                      # pragma: no cover - solo Windows
    msvcrt = None

from . import srxl2
from .transport import open_transport

LOW = srxl2.us_to_value(1000)
FULL = srxl2.us_to_value(2000)


def _frame(throttle):
    """Frame pieno: l'ESC non si arma con un canale solo, e in programmazione
    conviene comunque presentarsi come farebbe un ricevitore."""
    ch = dict((i, LOW) for i in range(8))
    ch[0] = throttle
    return ch


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    port = argv[0] if argv else "COM5"
    psu_target = argv[1] if len(argv) > 1 else None

    if msvcrt is None:
        print("questo strumento serve la tastiera e per ora gira solo su Windows")
        return 1

    psu = None
    if psu_target:
        sys.path.insert(0, "d:/work/Lab_Tools")
        from labtools.devices import UDP6952
        psu = UDP6952.connect(psu_target)

    print(__doc__)
    print("=" * 70)
    if psu:
        print("alimentatore pilotato: l'ESC viene acceso dal programma")
    else:
        print("ALIMENTA L'ESC QUANDO TE LO DICO")
    print()

    throttle = [FULL]
    with open_transport("esp32", port) as br:
        br.set_baud(srxl2.BAUD_LOW)
        br.reset()
        br.report_echo(False)

        def power_cycle():
            if psu:
                psu.set_output(False)
            print("   ... gas al massimo sul filo")
            throttle[0] = FULL
            t0 = time.monotonic()
            while time.monotonic() - t0 < 3.0:
                br.write(srxl2.control_data(_frame(FULL)))
                time.sleep(0.02)
            if psu:
                psu.set_output(True)
                print("   ... ESC acceso, ascolta i toni")
            else:
                print("   ... COLLEGA LA BATTERIA ADESSO, poi ascolta i toni")

        power_cycle()
        print()
        print("   B = minimo (entra)    F = massimo (conferma)    R = riaccendi    Q = esci")
        print()

        nxt = 0.0
        try:
            while True:
                now = time.monotonic()
                if now >= nxt:
                    nxt = now + 0.02
                    br.write(srxl2.control_data(_frame(throttle[0])))
                br.poll()

                if msvcrt.kbhit():
                    key = msvcrt.getch().decode("ascii", "ignore").lower()
                    if key == "b":
                        throttle[0] = LOW
                        print("   gas MINIMO      %s" % time.strftime("%H:%M:%S"))
                    elif key == "f":
                        throttle[0] = FULL
                        print("   gas MASSIMO     %s" % time.strftime("%H:%M:%S"))
                    elif key == "r":
                        power_cycle()
                    elif key == "q":
                        break
                time.sleep(0.002)
        finally:
            for _ in range(20):
                br.write(srxl2.control_data(_frame(LOW)))
                time.sleep(0.02)
            if psu:
                psu.set_output(False)
            print()
            print("gas al minimo, uscita spenta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
