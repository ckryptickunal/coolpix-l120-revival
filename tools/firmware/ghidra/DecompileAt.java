// Decompile the function containing each address given as a script argument.
// Usage (headless):
//   analyzeHeadless <projdir> l120 -process main.bin -noanalysis \
//     -scriptPath tools/firmware/ghidra -postScript DecompileAt.java 0x2fa680 0x2a9ea0
// If no function exists at an address, one is created first.
// @category L120

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileAt extends GhidraScript {
    @Override
    protected void run() throws Exception {
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        for (String arg : getScriptArgs()) {
            Address a = toAddr(Long.decode(arg));
            Function f = getFunctionContaining(a);
            if (f == null) {
                disassemble(a);
                f = createFunction(a, null);
            }
            if (f == null) {
                println("== " + arg + ": no function");
                continue;
            }
            DecompileResults r = di.decompileFunction(f, 120, monitor);
            println("== " + arg + " in " + f.getName() + " @ " + f.getEntryPoint());
            StringBuilder callers = new StringBuilder();
            for (Function c : f.getCallingFunctions(monitor)) {
                callers.append(c.getEntryPoint()).append(' ');
            }
            println("   callers: " + callers);
            println(r.getDecompiledFunction() == null ? r.getErrorMessage()
                    : r.getDecompiledFunction().getC());
        }
    }
}
