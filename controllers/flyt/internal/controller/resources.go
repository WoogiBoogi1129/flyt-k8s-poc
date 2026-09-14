package controller

import (
    "fmt"
    "strconv"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    appsv1 "k8s.io/api/apps/v1"
    corev1 "k8s.io/api/core/v1"
    netv1 "k8s.io/api/networking/v1"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/api/resource"
    "k8s.io/apimachinery/pkg/util/intstr"
    "sigs.k8s.io/controller-runtime/pkg/client"
)
func boolp(v bool)*bool{return &v}
func int32p(v int32)*int32{return &v}
func int64p(v int64)*int64{return &v}
func port(n int) *intstr.IntOrString { v:=intstr.FromInt(n);return &v }
func proto(v corev1.Protocol)*corev1.Protocol{return &v}
func metaFor(owner client.Object,name,role string) metav1.ObjectMeta {
    return metav1.ObjectMeta{Name:name,Namespace:owner.GetNamespace(),Labels:labels(owner,role)}
}
func dnsRule() netv1.NetworkPolicyEgressRule {
    return netv1.NetworkPolicyEgressRule{To:[]netv1.NetworkPolicyPeer{{
        NamespaceSelector:&metav1.LabelSelector{MatchLabels:map[string]string{"kubernetes.io/metadata.name":"kube-system"}},
        PodSelector:&metav1.LabelSelector{MatchLabels:map[string]string{"k8s-app":"kube-dns"}}}},
        Ports:[]netv1.NetworkPolicyPort{{Protocol:proto(corev1.ProtocolUDP),Port:port(53)}, {Protocol:proto(corev1.ProtocolTCP),Port:port(53)}}}
}
func dnsHost(name,namespace,domain string)string{return fmt.Sprintf("%s.%s.svc.%s",name,namespace,domain)}
func cfg(owner client.Object,name,role,filename,contents string)*corev1.ConfigMap {
    return &corev1.ConfigMap{ObjectMeta:metaFor(owner,name,role),Immutable:boolp(true),Data:map[string]string{filename:contents}}
}
func svc(owner client.Object,name,role string,ports []corev1.ServicePort,headless bool)*corev1.Service {
    s:=&corev1.Service{ObjectMeta:metaFor(owner,name,role),Spec:corev1.ServiceSpec{Selector:labels(owner,role),Ports:ports,
        Type:corev1.ServiceTypeClusterIP,IPFamilies:[]corev1.IPFamily{corev1.IPv4Protocol},IPFamilyPolicy:func()*corev1.IPFamilyPolicy{p:=corev1.IPFamilyPolicySingleStack;return &p}()}}
    if headless{s.Spec.ClusterIP="None";s.Spec.PublishNotReadyAddresses=true};return s
}
func deploy(owner client.Object,name,role string,container corev1.Container,pull []string)*appsv1.Deployment {
    container.VolumeMounts=append(container.VolumeMounts,corev1.VolumeMount{Name:"config",MountPath:"/etc/flyt",ReadOnly:true},
        corev1.VolumeMount{Name:"run",MountPath:"/run/flyt"},corev1.VolumeMount{Name:"tmp",MountPath:"/tmp"})
    s:=corev1.PodSpec{AutomountServiceAccountToken:boolp(false),EnableServiceLinks:boolp(false),TerminationGracePeriodSeconds:int64p(30),
        Containers:[]corev1.Container{container},Volumes:[]corev1.Volume{
            {Name:"config",VolumeSource:corev1.VolumeSource{ConfigMap:&corev1.ConfigMapVolumeSource{LocalObjectReference:corev1.LocalObjectReference{Name:name+"-config"}}}},
            {Name:"run",VolumeSource:corev1.VolumeSource{EmptyDir:&corev1.EmptyDirVolumeSource{}}},
            {Name:"tmp",VolumeSource:corev1.VolumeSource{EmptyDir:&corev1.EmptyDirVolumeSource{}}}}}
    for _,n:=range pull{s.ImagePullSecrets=append(s.ImagePullSecrets,corev1.LocalObjectReference{Name:n})}
    return &appsv1.Deployment{ObjectMeta:metaFor(owner,name,role),Spec:appsv1.DeploymentSpec{Replicas:int32p(1),
        Strategy:appsv1.DeploymentStrategy{Type:appsv1.RecreateDeploymentStrategyType},RevisionHistoryLimit:int32p(1),
        Selector:&metav1.LabelSelector{MatchLabels:labels(owner,role)},Template:corev1.PodTemplateSpec{ObjectMeta:metav1.ObjectMeta{Labels:labels(owner,role)},Spec:s}}}
}
func planeResources(p *api.FlytControlPlane) []client.Object {
    name:=planeName(p)
    config:=`[ports]
node = 12401
client = 12402
[metrics]
port = 12403
interval = 30
[virt-server-auto-deallocate]
enabled = true
grace-period = 10
[ipc]
mqueue-path = "/tmp/flyt-rmgr-queue"
frontend-socket = "/run/flyt/flyt-frontend-socket"
[migration]
ckp-path = "/tmp/flytckp"
`
    container:=corev1.Container{Name:"manager",Image:p.Spec.ManagerImage,
        Env:[]corev1.EnvVar{{Name:"FLYT_RESOURCE_BACKEND",Value:"hami"},{Name:"FLYT_BINDING_API",Value:"1"},
            {Name:"FLYT_BINDING_TOKEN_FILE",Value:"/etc/flyt-auth/token"},{Name:"RUST_LOG",Value:"info"}},
        Resources:corev1.ResourceRequirements{Requests:corev1.ResourceList{corev1.ResourceCPU:resource.MustParse("100m"),corev1.ResourceMemory:resource.MustParse("128Mi")},
            Limits:corev1.ResourceList{corev1.ResourceCPU:resource.MustParse("2"),corev1.ResourceMemory:resource.MustParse("1Gi")}},
        SecurityContext:&corev1.SecurityContext{AllowPrivilegeEscalation:boolp(false),ReadOnlyRootFilesystem:boolp(true),Capabilities:&corev1.Capabilities{Drop:[]corev1.Capability{"ALL"}}},
        VolumeMounts:[]corev1.VolumeMount{{Name:"auth",MountPath:"/etc/flyt-auth",ReadOnly:true}},
        ReadinessProbe:&corev1.Probe{ProbeHandler:corev1.ProbeHandler{TCPSocket:&corev1.TCPSocketAction{Port:intstr.FromInt(12404)}},PeriodSeconds:5}}
    d:=deploy(p,name,"manager",container,p.Spec.ImagePullSecrets)
    d.Spec.Template.Spec.SecurityContext=&corev1.PodSecurityContext{RunAsUser:int64p(65532),RunAsGroup:int64p(65532),RunAsNonRoot:boolp(true),FSGroup:int64p(65532)}
    d.Spec.Template.Spec.Volumes=append(d.Spec.Template.Spec.Volumes,corev1.Volume{Name:"auth",VolumeSource:corev1.VolumeSource{Secret:&corev1.SecretVolumeSource{SecretName:p.Spec.AuthSecretName,DefaultMode:int32p(0440)}}})
    workers:=map[string]string{Experiment:Managed,PlaneLabel:string(p.UID),"flyt.dev/role":"worker"}
    n:=&netv1.NetworkPolicy{ObjectMeta:metaFor(p,name,"manager"),Spec:netv1.NetworkPolicySpec{
        PodSelector:metav1.LabelSelector{MatchLabels:labels(p,"manager")},PolicyTypes:[]netv1.PolicyType{netv1.PolicyTypeIngress,netv1.PolicyTypeEgress},
        Ingress:[]netv1.NetworkPolicyIngressRule{
            {From:[]netv1.NetworkPolicyPeer{{PodSelector:&metav1.LabelSelector{MatchLabels:workers}}},Ports:[]netv1.NetworkPolicyPort{{Protocol:proto(corev1.ProtocolTCP),Port:port(12401)}}},
            {From:[]netv1.NetworkPolicyPeer{{PodSelector:&metav1.LabelSelector{MatchLabels:map[string]string{"app.kubernetes.io/name":"flyt-controller"}}}},Ports:[]netv1.NetworkPolicyPort{{Protocol:proto(corev1.ProtocolTCP),Port:port(12404)}}}},
        Egress:[]netv1.NetworkPolicyEgressRule{dnsRule(),{To:[]netv1.NetworkPolicyPeer{{PodSelector:&metav1.LabelSelector{MatchLabels:workers}}},Ports:[]netv1.NetworkPolicyPort{{Protocol:proto(corev1.ProtocolTCP)}}}}}}
    return []client.Object{cfg(p,name+"-config","manager","cluster-mgr.toml",config),
        svc(p,name,"manager",[]corev1.ServicePort{{Name:"nodes",Port:12401,TargetPort:intstr.FromInt(12401)},{Name:"clients",Port:12402,TargetPort:intstr.FromInt(12402)},{Name:"bindings",Port:12404,TargetPort:intstr.FromInt(12404)}},false),n,d}
}
func workerResources(w *api.FlytWorker,p *api.FlytGPUProfile,cp *api.FlytControlPlane,vmIP string) []client.Object {
    name:=w.Name
    config:=fmt.Sprintf(`[resource-manager]
address = %q
port = 12401
[virt-server]
program-path = "/opt/flyt/bin/cricket-rpc-server"
thread-mode = 0
program-args = ""
[ipc]
mqueue-path = "/tmp/flyt-servernode-queue"
`,dnsHost(planeName(cp),cp.Namespace,cp.Spec.ClusterDomain))
    q:=allocation(w,p)
    quota:=corev1.ResourceList{corev1.ResourceName("nvidia.com/gpu"):resource.MustParse(strconv.Itoa(int(q.Count))),
        corev1.ResourceName("nvidia.com/gpumem"):resource.MustParse(strconv.FormatInt(q.MemoryMiB,10)),
        corev1.ResourceName("nvidia.com/gpucores"):resource.MustParse(strconv.Itoa(int(q.Compute)))}
    requests:=quota.DeepCopy();requests[corev1.ResourceCPU]=resource.MustParse("250m");requests[corev1.ResourceMemory]=resource.MustParse("512Mi")
    limits:=quota.DeepCopy();limits[corev1.ResourceCPU]=resource.MustParse("4");limits[corev1.ResourceMemory]=resource.MustParse("4Gi")
    container:=corev1.Container{Name:"worker",Image:p.Spec.WorkerImage,Resources:corev1.ResourceRequirements{Requests:requests,Limits:limits},
        Env:[]corev1.EnvVar{{Name:"FLYT_RESOURCE_BACKEND",Value:"hami"},{Name:"FLYT_BINDING_API",Value:"1"},
            {Name:"FLYT_GPU_UUID",Value:p.Spec.GPUUUID},{Name:"FLYT_MEMORY_BYTES",Value:strconv.FormatInt(q.MemoryMiB*1024*1024,10)},
            {Name:"FLYT_MAX_CLIENTS",Value:strconv.Itoa(int(p.Spec.MaxClients))},{Name:"GPU_CORE_UTILIZATION_POLICY",Value:"force"},
            {Name:"RUST_LOG",Value:"info"},{Name:"FLYT_POD_UID",ValueFrom:&corev1.EnvVarSource{FieldRef:&corev1.ObjectFieldSelector{FieldPath:"metadata.uid"}}}},
        SecurityContext:&corev1.SecurityContext{RunAsUser:int64p(0),AllowPrivilegeEscalation:boolp(false),Capabilities:&corev1.Capabilities{Drop:[]corev1.Capability{"ALL"},Add:[]corev1.Capability{"SETUID","SETGID","NET_BIND_SERVICE","KILL"}}},
        ReadinessProbe:&corev1.Probe{ProbeHandler:corev1.ProbeHandler{Exec:&corev1.ExecAction{Command:[]string{"python3","/opt/flyt/worker.py","ready"}}},PeriodSeconds:5,TimeoutSeconds:4,FailureThreshold:2}}
    d:=deploy(w,name,"worker",container,cp.Spec.ImagePullSecrets)
    d.Spec.Template.Annotations=map[string]string{"nvidia.com/use-gpuuuid":p.Spec.GPUUUID,"nvidia.com/vgpu-mode":"hami-core"}
    d.Spec.Template.Spec.SchedulerName=p.Spec.SchedulerName
    d.Spec.Template.Spec.NodeSelector=map[string]string{"kubernetes.io/hostname":p.Spec.NodeName}
    d.Spec.Template.Spec.Tolerations=[]corev1.Toleration{{Key:"node-role.kubernetes.io/control-plane",Operator:corev1.TolerationOpExists,Effect:corev1.TaintEffectNoSchedule}}
    if p.Spec.RuntimeClass!=""{d.Spec.Template.Spec.RuntimeClassName=&p.Spec.RuntimeClass}
    vm:=netv1.NetworkPolicyPeer{IPBlock:&netv1.IPBlock{CIDR:vmIP+"/32"}}
    manager:=netv1.NetworkPolicyPeer{PodSelector:&metav1.LabelSelector{MatchLabels:labels(cp,"manager")}}
    network:=&netv1.NetworkPolicy{ObjectMeta:metaFor(w,name,"worker"),Spec:netv1.NetworkPolicySpec{
        PodSelector:metav1.LabelSelector{MatchLabels:labels(w,"worker")},PolicyTypes:[]netv1.PolicyType{netv1.PolicyTypeIngress,netv1.PolicyTypeEgress},
        Ingress:[]netv1.NetworkPolicyIngressRule{{From:[]netv1.NetworkPolicyPeer{vm},Ports:[]netv1.NetworkPolicyPort{{Protocol:proto(corev1.ProtocolTCP)}}}},
        Egress:[]netv1.NetworkPolicyEgressRule{dnsRule(),{To:[]netv1.NetworkPolicyPeer{manager},Ports:[]netv1.NetworkPolicyPort{{Protocol:proto(corev1.ProtocolTCP),Port:port(12401)}}}}}}
    // One independently owned ingress rule per VMI; the shared manager Deployment
    // and ConfigMap never change when the set of VMs changes.
    ingress:=&netv1.NetworkPolicy{ObjectMeta:metaFor(w,name+"-manager","worker"),Spec:netv1.NetworkPolicySpec{
        PodSelector:metav1.LabelSelector{MatchLabels:labels(cp,"manager")},PolicyTypes:[]netv1.PolicyType{netv1.PolicyTypeIngress},
        Ingress:[]netv1.NetworkPolicyIngressRule{{From:[]netv1.NetworkPolicyPeer{vm},Ports:[]netv1.NetworkPolicyPort{{Protocol:proto(corev1.ProtocolTCP),Port:port(12402)}}}}}}
    return []client.Object{cfg(w,name+"-config","worker","node-mgr.toml",config),svc(w,name,"worker",[]corev1.ServicePort{{Name:"rpcbind",Port:111,TargetPort:intstr.FromInt(111)}},true),network,ingress,d}
}
