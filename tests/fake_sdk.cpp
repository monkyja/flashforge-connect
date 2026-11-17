// Test double for the public FlashNetwork 3.4.0 header. No network or printer access.
#include "FlashNetwork-3.4.0.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <thread>

static int outstanding = 0;
static int passwordLogins = 0, refreshes = 0;
static char accessToken[] = "synthetic-access-token";
static char refreshToken[] = "synthetic-refresh-token";
static double current[] = {210, 25, 25, 25};
static double target[] = {210, 0, 0, 0};
static fnet_matl_slot_info_t slots[] = {{1, 1, (char*)"PLA simulado", (char*)"#ffffff"}};
static int uploads = 0, jobs = 0, dispatches = 0, lastSlot = -1, lastPlate = -1;
static int cameraOpens = 0;
static char lastMd5[64]{};
static bool fails(const char* phase) {
    const char* value = std::getenv("FAKE_PRINT_FAIL");
    return value && std::strcmp(value, phase) == 0;
}
struct FakeConnection { fnet_conn_settings_t settings; };

extern "C" {
int test_outstanding() { return outstanding; }
int test_password_logins() { return passwordLogins; }
int test_refreshes() { return refreshes; }
int test_uploads() { return uploads; }
int test_jobs() { return jobs; }
int test_dispatches() { return dispatches; }
int test_camera_opens() { return cameraOpens; }
int test_slot() { return lastSlot; }
int test_plate() { return lastPlate; }
const char* test_md5() { return lastMd5; }
void test_reset_print() { uploads = jobs = dispatches = 0; lastSlot = lastPlate = -1; }
const char* fnet_getVersion() {
    const char* version = std::getenv("FAKE_SDK_VERSION");
    return version ? version : "3.4.0";
}
int fnet_initlize(const char*, const fnet_log_settings_t*) {
    std::puts("Native SDK output must not corrupt RPC");
    return FNET_OK;
}
void fnet_uninitlize() {}
void fnet_setUserAgent(const char*) {}
int fnet_getTokenByPassword(const char*, const char* password, const char*,
                           fnet_token_data_t** token, char** message, int) {
    ++passwordLogins;
    if (std::strcmp(password, "bad") == 0) {
        *message = ::strdup("sensitive native diagnostic");
        ++outstanding;
        return FNET_INVALID_VALIDATION;
    }
    *token = new fnet_token_data_t{3600, accessToken, refreshToken};
    ++outstanding;
    return FNET_OK;
}
int fnet_refreshToken(const char* refresh, const char*, fnet_token_data_t** token, char**, int) {
    ++refreshes;
    if (std::strcmp(refresh, "expired") == 0) return FNET_UNAUTHORIZED;
    *token = new fnet_token_data_t{3600, accessToken, refreshToken};
    ++outstanding;
    return FNET_OK;
}
void fnet_freeToken(fnet_token_data_t* token) { delete token; --outstanding; }
void fnet_freeString(const char* value) { std::free((void*)value); --outstanding; }
char* fnet_allocString(const char* value, int len) { ++outstanding; return ::strndup(value, len); }
int fnet_getUserProfile(const char*, fnet_user_profile_t** profile, int) {
    *profile = new fnet_user_profile_t{(char*)"synthetic-user", (char*)"Cuenta de prueba",
                                     (char*)"", (char*)"synthetic@example.invalid"};
    ++outstanding;
    return FNET_OK;
}
void fnet_freeUserProfile(fnet_user_profile_t* profile) { delete profile; --outstanding; }
int fnet_getWanDevList(const char*, const char*, long long, long long,
                      fnet_wan_dev_info_t** devices, int* count, int) {
    *count = 1;
    *devices = new fnet_wan_dev_info_t[1]{};
    (*devices)[0].devId = (char*)"synthetic-device";
    (*devices)[0].name = (char*)"Creator de prueba";
    (*devices)[0].model = (char*)"Creator 5 Pro";
    (*devices)[0].status = (char*)"printing";
    (*devices)[0].serialNumber = (char*)"SYNTHETIC-SERIAL";
    (*devices)[0].devTopic = (char*)"synthetic/topic";
    ++outstanding;
    return FNET_OK;
}
void fnet_freeWanDevList(fnet_wan_dev_info_t* devices, int) { delete[] devices; --outstanding; }
int fnet_getWanDevProductDetail(const char*, const char*, const char*, fnet_dev_product_t** product,
                               fnet_dev_detail_t** detail, int) {
    *product = new fnet_dev_product_t{};
    *detail = new fnet_dev_detail_t{};
    auto& d = **detail;
    d.name = (char*)"Creator de prueba";
    d.firmwareVersion = (char*)"simulated";
    d.status = (char*)"printing";
    d.printFileName = (char*)"synthetic.gcode";
    d.printProgress = .42;
    d.printLayer = 84;
    d.targetPrintLayer = 200;
    d.nozzleCnt = 4;
    d.nozzleTemps = current;
    d.nozzleTargetTemps = target;
    d.platTemp = 60;
    d.platTargetTemp = 60;
    d.matlStationInfo.slotCnt = 1;
    d.matlStationInfo.slotInfos = slots;
    outstanding += 2;
    return FNET_OK;
}
void fnet_freeDevDetail(const fnet_dev_detail_t* detail) { delete detail; --outstanding; }
void fnet_freeDevProduct(fnet_dev_product_t* product) { delete product; --outstanding; }
int fnet_bindAccountRelp(const char*, const char*, const char*, fnet_bind_account_relp_result_t** result, int) {
    *result = new fnet_bind_account_relp_result_t{}; ++outstanding; return FNET_OK;
}
void fnet_freeBindAccountRelpResult(fnet_bind_account_relp_result_t* result) { delete result; --outstanding; }
int fnet_getMqttConfig(const char*, const char*, fnet_mqtt_config_t** config, int) {
    *config = new fnet_mqtt_config_t{}; ++outstanding; return FNET_OK;
}
void fnet_freeMqttConfig(fnet_mqtt_config_t* config) { delete config; --outstanding; }
int fnet_createConnection(void** conn, const fnet_conn_settings_t* settings) {
    auto* value = new FakeConnection{*settings}; *conn = value; ++outstanding;
    const char* clientId = nullptr;
    if (settings->updateCallback(&clientId, settings->updateCallbackData) != 0) return FNET_ERROR;
    fnet_freeString(clientId);
    settings->statusCallback(FNET_CONN_STATUS_CONNECTED, settings->statusCallbackData);
    return FNET_OK;
}
void fnet_connectionStop(void* conn) {
    auto* value = (FakeConnection*)conn;
    value->settings.statusCallback(FNET_CONN_STATUS_DISCONNECTED, value->settings.statusCallbackData);
}
void fnet_freeConnection(void* conn) { delete (FakeConnection*)conn; --outstanding; }
int fnet_connectionSend(void*, const fnet_conn_write_data_t* data) {
    if (data->type != FNET_CONN_WRITE_CAMERA_STREAM_CTRL || data->qos != 1 ||
        std::strcmp(data->topic, "synthetic/topic") != 0 ||
        std::strcmp(((const fnet_camera_stream_ctrl_t*)data->data)->action, "open") != 0) return FNET_ERROR;
    ++cameraOpens;
    return FNET_OK;
}
int fnet_wanDevSendGcodeClound(const char*, const char*, const fnet_send_gcode_data_t* data,
                              fnet_clound_gcode_data_t** cloud, int) {
    ++uploads;
    *cloud = new fnet_clound_gcode_data_t{(char*)"synthetic-bucket", (char*)"synthetic-endpoint",
                                        (char*)"gcode-key", (char*)"gcode-url", (char*)"thumb-key", (char*)"thumb-url"};
    ++outstanding;
    for (int i = 0; i <= 100; ++i) {
        if (data->callback(i, 100, data->callbackData)) return FNET_ABORTED_BY_CALLBACK;
        if (std::getenv("FAKE_UPLOAD_SLOW")) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    if (fails("upload")) return FNET_ERROR;
    return FNET_OK;
}
void fnet_freeCloundGcodeData(fnet_clound_gcode_data_t* cloud) { delete cloud; --outstanding; }
int fnet_wanDevAddCloundJob(const char*, const char*, const fnet_clound_job_data_t* data,
                           fnet_add_clound_job_result_t** results, int* count, int) {
    ++jobs; *count = 1;
    *results = new fnet_add_clound_job_result_t[1]{{fails("busy") ? FNET_ADD_CLOUND_JOB_DEVICE_BUSY : FNET_ADD_CLOUND_JOB_OK,
                                                 (char*)"synthetic-device", (char*)"synthetic-job"}};
    ++outstanding;
    lastPlate = data->plateNo;
    lastSlot = data->gcodeToolCnt ? data->materialMappings[0].slotId : -1;
    std::snprintf(lastMd5, sizeof(lastMd5), "%s", data->gcodeMd5);
    if (fails("create")) return FNET_ERROR;
    return FNET_OK;
}
void fnet_freeAddCloudJobResults(fnet_add_clound_job_result_t* results, int) { delete[] results; --outstanding; }
int fnet_connectionSendMulti(void*, const fnet_conn_write_multi_data_t* data,
                             fnet_conn_write_multi_result_t** result) {
    ++dispatches;
    if (data->type != FNET_CONN_WRITE_START_CLOUND_JOB || data->dataCnt != 1 || data->topicCnt != 1) return FNET_ERROR;
    const auto* job = (const fnet_clound_job_data_t*)data->datas[0];
    if (job->devCnt != 1 || !job->devSerialNumbers || !job->jobIds ||
        std::strcmp(data->topics[0], "synthetic/topic") || std::strcmp(job->jobIds[0], "synthetic-job")) return FNET_ERROR;
    *result = new fnet_conn_write_multi_result_t{}; ++outstanding;
    if (fails("dispatch")) return FNET_CONN_SEND_ERROR;
    return FNET_OK;
}
void fnet_freeWriteMultiResult(const fnet_conn_write_multi_result_t* result) { delete result; --outstanding; }
void fnet_freeSyncLoginInfo(const fnet_sync_login_info_t* data) { std::free((void*)data); --outstanding; }
void fnet_freeSyncBindInfo(const fnet_sync_bind_info_t* data) { std::free((void*)data); --outstanding; }
void fnet_freeSyncOnlineInfo(const fnet_sync_online_info_t* data) { std::free((void*)data); --outstanding; }
void fnet_freeSliceState(const fnet_slice_state_t* data) { std::free((void*)data); --outstanding; }
void fnet_freeJobInfo(const fnet_job_info_t* data) { std::free((void*)data); --outstanding; }
void test_emit_read_events(void* conn) {
    auto* value = (FakeConnection*)conn;
    for (int type = 0; type <= 13; ++type) {
        fnet_conn_read_data_t event{}; event.type = (fnet_conn_read_data_type_t)type;
        event.topic = ::strdup("synthetic/topic"); ++outstanding;
        if (type == 9) event.data = new fnet_dev_detail_t{};
        else if (type != 2 && type != 3) event.data = std::calloc(1, 256);
        if (event.data) ++outstanding;
        value->settings.readCallback(&event, value->settings.readCallbackData);
    }
}
}
