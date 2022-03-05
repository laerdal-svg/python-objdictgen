import os
import objdictgen.__main__


# def test_odsetup(odfile, fn):
#     """ Test that we have the same od files present in DUT as in REF """
#     reffile = odfile.replace(fn.DUT, fn.REF)
#     d = list(fn.diff(reffile + '.od', odfile + '.od'))
#     assert not d


def test_objdictgen(mocker, odfile, fn):
    """ Test that objdictgen generates equal output as reference """
    base, od = os.path.split(odfile)

    mocker.patch("sys.argv", [
        "objdictgen",
        odfile + '.od',
        od + '.c',
    ])

    objdictgen.__main__.main_objdictgen()

    assert fn.diff(odfile + '.c', od + '.c', n=0)
    assert fn.diff(odfile + '.h', od + '.h', n=0)
    assert fn.diff(odfile + '_objectdefines.h', od + '_objectdefines.h', n=0)
