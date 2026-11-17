/* Точка композиции Workwear: модули подключаются явно, без глобального store.
 * Общие функции UI берутся из Portal. Предметные инварианты проверяет сервер. */
import { cycleFocus, syncModalInert, iconSvg } from '../portal/ui.js';
import { installCore } from './js/core.js';
import { installBatching } from './js/batching.js';
import { installPaging } from './js/paging.js';
import { installRegistry } from './js/registry.js';
import { installEditor } from './js/editor.js';
import { installEmployees } from './js/employees.js';
import { installOverlays } from './js/overlays.js';
import { installController } from './js/controller.js';
const context = Object.assign(Object.create(null), { cycleFocus, syncModalInert, platformIcon: iconSvg });
if (installCore(context)) {
    installBatching(context);
    installPaging(context);
    installRegistry(context);
    installEditor(context);
    installEmployees(context);
    installOverlays(context);
    installController(context);
    void context.start().catch(context.fail);
}
