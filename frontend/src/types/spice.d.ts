// The parts of spice-html5 (LGPL-3.0, loaded unmodified; D24) that we use.
declare module "@spice-project/spice-html5" {
  export interface SpiceMainConnOptions {
    uri: string;
    screen_id: string;
    password?: string;
    onerror?: (e: Error) => void;
    onsuccess?: () => void;
    onagent?: (sc: SpiceMainConn) => void;
  }
  export class SpiceMainConn {
    constructor(o: SpiceMainConnOptions);
    stop(): void;
  }
  export function sendCtrlAltDel(sc: SpiceMainConn): void;
}
