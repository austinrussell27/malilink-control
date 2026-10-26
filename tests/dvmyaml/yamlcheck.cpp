// Parses a generated config with dvmhost's own YAML parser (src/common/yaml) and prints the values
// the way Host.Config.cpp reads them. Build: see build.sh. Used by tests/test_dvm_yaml.py if present.
#include "common/yaml/Yaml.h"
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv) {
    yaml::Node c;
    if (!yaml::Parse(c, argv[1])) { std::printf("PARSE_FAIL\n"); return 1; }
    yaml::Node sc = c["system"]["config"];
    std::printf("iAgree=%d\n", c["iAgreeNotToBeStupid"].as<bool>(false));
    std::printf("p25.enable=%d control.enable=%d dedicated=%d\n", c["protocols"]["p25"]["enable"].as<bool>(false),
        c["protocols"]["p25"]["control"]["enable"].as<bool>(false), c["protocols"]["p25"]["control"]["dedicated"].as<bool>(false));
    std::printf("authoritative=%d supervisor=%d\n", sc["authoritative"].as<bool>(true), sc["supervisor"].as<bool>(false));
    std::printf("channelId=%u channelNo=%lu nac=%lX sysId=%lX netId=%lX rfss=%lX site=%lX\n", sc["channelId"].as<uint32_t>(0U),
        std::strtoul(sc["channelNo"].as<std::string>("1").c_str(), NULL, 16), std::strtoul(sc["nac"].as<std::string>("F7E").c_str(), NULL, 16),
        std::strtoul(sc["sysId"].as<std::string>("001").c_str(), NULL, 16), std::strtoul(sc["netId"].as<std::string>("BB800").c_str(), NULL, 16),
        std::strtoul(sc["rfssId"].as<std::string>("1").c_str(), NULL, 16), std::strtoul(sc["siteId"].as<std::string>("1").c_str(), NULL, 16));
    yaml::Node cc = sc["controlCh"];
    std::printf("controlCh=%s:%u pw=%s notify=%d\n", cc["rpcAddress"].as<std::string>("").c_str(), cc["rpcPort"].as<uint32_t>(9890U),
        cc["rpcPassword"].as<std::string>().c_str(), cc["notifyEnable"].as<bool>(false));
    yaml::Node& vl = sc["voiceChNo"];
    std::printf("voiceChNo.size=%zu\n", vl.size());
    for (size_t i = 0; i < vl.size(); i++) {
        yaml::Node& ch = vl[i];
        std::printf("vc[%zu] id=%u no=%lu rpc=%s:%u pw=%s\n", i, ch["channelId"].as<uint32_t>(255U),
            std::strtoul(ch["channelNo"].as<std::string>("1").c_str(), NULL, 16), ch["rpcAddress"].as<std::string>("0.0.0.0").c_str(),
            ch["rpcPort"].as<uint32_t>(9890U), ch["rpcPassword"].as<std::string>().c_str());
    }
    yaml::Node n = c["network"];
    std::printf("network.enable=%d addr=%s:%u id=%u rpcAddress=%s rpcPw=%s restEnable=%d restPw=%s\n", n["enable"].as<bool>(false),
        n["address"].as<std::string>().c_str(), n["port"].as<uint32_t>(0U), n["id"].as<uint32_t>(0U), n["rpcAddress"].as<std::string>().c_str(),
        n["rpcPassword"].as<std::string>().c_str(), n["restEnable"].as<bool>(false), n["restPassword"].as<std::string>().c_str());
    std::printf("modem=%s %s cwid=%s identity=%s\n", c["system"]["modem"]["protocol"]["type"].as<std::string>().c_str(),
        c["system"]["modem"]["protocol"]["uart"]["port"].as<std::string>().c_str(), c["system"]["cwId"]["callsign"].as<std::string>().c_str(),
        c["system"]["identity"].as<std::string>().c_str());
    return 0;
}
