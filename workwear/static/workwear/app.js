/* Подключаем модули интерфейса. Бизнес-правила проверяет сервер. */
import { installCore } from './js/core.js';
import { installBatching } from './js/batching.js';
import { installPaging } from './js/paging.js';
import { installRegistry } from './js/registry.js';
import { installEditor } from './js/editor.js';
import { installEmployees } from './js/employees.js';
import { installOverlays } from './js/overlays.js';
import { installController } from './js/controller.js';
const context = Object.create(null);
if (installCore(context)) {
    installBatching(context);
    installPaging(context);
    installRegistry(context);
    installEditor(context);
    installEmployees(context);
    installOverlays(context);
    const start = installController(context);
    void start().catch(context.fail);
}
