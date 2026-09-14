package controller

import (
    "context"
    "encoding/json"
    "errors"
    "fmt"
    "regexp"
    "strconv"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
    "k8s.io/apimachinery/pkg/types"
    "sigs.k8s.io/controller-runtime/pkg/client"
)

const RequestAnnotation="flyt.dev/gpu-request"
const SnapshotAnnotation="flyt.dev/request-snapshot"
const LegacyVMIAnnotation="flyt.dev/legacy-vmi"
var errVMIdentity=errors.New("VM UID is stale or VM is terminating")
type requestSnapshot struct {
    VMIUID string `json:"vmiUID"`
    Allocation api.RequestAllocation `json:"allocation"`
    ProfileRef api.Reference `json:"profileRef"`
    ControlPlaneRef api.Reference `json:"controlPlaneRef"`
}
var memoryPattern=regexp.MustCompile(`^([1-9][0-9]{0,6})(Mi|Gi)$`)

func normalizeRequest(s api.GPURequestSpec)(api.GPUResources,error){
    q:=api.GPUResources{Count:s.Count,Compute:s.Compute}
    m:=memoryPattern.FindStringSubmatch(s.Memory)
    if len(m)!=3{return q,fmt.Errorf("memory must be a positive integer followed by Mi or Gi")}
    n,err:=strconv.ParseInt(m[1],10,64);if err!=nil{return q,err}
    if m[2]=="Gi"{n*=1024};q.MemoryMiB=n
    return q,validateResources(q)
}
func validateResources(q api.GPUResources)error{
    if q.Count!=1{return fmt.Errorf("count must be 1; multi-GPU remoting is not supported")}
    if q.Compute<1||q.Compute>100{return fmt.Errorf("compute must be an integer percentage in 1..100")}
    if q.MemoryMiB<256||q.MemoryMiB>1048576{return fmt.Errorf("memory must be 256..1048576 MiB")}
    return nil
}
func withinProfile(q api.GPUResources,p *api.FlytGPUProfile)error{
    if err:=validateResources(q);err!=nil{return err}
    if !p.Spec.Approved{return fmt.Errorf("GPU profile is not approved")}
    if q.Compute>p.Spec.Cores||q.MemoryMiB>p.Spec.MemoryMiB{return fmt.Errorf("request exceeds profile ceiling: compute <= %d, memoryMiB <= %d",p.Spec.Cores,p.Spec.MemoryMiB)}
    return nil
}
func virtualMachine()*unstructured.Unstructured{v:=&unstructured.Unstructured{};v.SetGroupVersionKind(VMGVK);return v}
func (b *Base) requestVM(ctx context.Context,r *api.FlytGPURequest)(*unstructured.Unstructured,error){
    vm:=virtualMachine()
    if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:r.Namespace,Name:r.Spec.VMRef.Name},vm);err!=nil{return nil,err}
    if string(vm.GetUID())!=r.Spec.VMRef.UID||!vm.GetDeletionTimestamp().IsZero(){return nil,errVMIdentity}
    if len(r.OwnerReferences)>0{
        if len(r.OwnerReferences)!=1{return nil,fmt.Errorf("request has conflicting owners")}
        owner:=r.OwnerReferences[0]
        if owner.UID!=vm.GetUID()||owner.Name!=vm.GetName()||owner.Kind!="VirtualMachine"||owner.APIVersion!="kubevirt.io/v1"{return nil,fmt.Errorf("request has a conflicting VM owner")}
    }
    return vm,nil
}
func matchesVM(v *unstructured.Unstructured,ref api.Reference)bool{
    owner:=metav1.GetControllerOf(v)
    return v.GetName()==ref.Name&&owner!=nil&&owner.APIVersion=="kubevirt.io/v1"&&owner.Kind=="VirtualMachine"&&owner.Name==ref.Name&&string(owner.UID)==ref.UID
}
func (b *Base) requestDependencies(ctx context.Context,r *api.FlytGPURequest)(*api.FlytGPUProfile,*api.FlytControlPlane,error){
    p:=&api.FlytGPUProfile{};cp:=&api.FlytControlPlane{}
    if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:r.Namespace,Name:r.Spec.ProfileRef.Name},p);err!=nil{return nil,nil,err}
    if string(p.UID)!=r.Spec.ProfileRef.UID{return nil,nil,fmt.Errorf("Profile UID is stale")}
    if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:r.Namespace,Name:r.Spec.ControlPlaneRef.Name},cp);err!=nil{return nil,nil,err}
    if string(cp.UID)!=r.Spec.ControlPlaneRef.UID{return nil,nil,fmt.Errorf("ControlPlane UID is stale")}
    return p,cp,nil
}
func readSnapshot(v *unstructured.Unstructured)(*requestSnapshot,error){
    raw:=v.GetAnnotations()[SnapshotAnnotation];if raw==""{return nil,nil}
    if len(raw)>8192{return nil,fmt.Errorf("request snapshot is too large")}
    s:=&requestSnapshot{}
    if err:=json.Unmarshal([]byte(raw),s);err!=nil{return nil,fmt.Errorf("invalid request snapshot JSON")}
    if s.VMIUID!=string(v.GetUID())||s.Allocation.RequestRef.Name==""||s.Allocation.RequestRef.UID==""||s.Allocation.Generation<1||!matchesVM(v,s.Allocation.VMRef){return nil,fmt.Errorf("invalid request snapshot identity")}
    if err:=validateResources(s.Allocation.Resources);err!=nil{return nil,err}
    return s,nil
}
func (b *Base) writeSnapshot(ctx context.Context,v *unstructured.Unstructured,s *requestSnapshot)error{
    old:=v.DeepCopy();annotations:=v.GetAnnotations();if annotations==nil{annotations=map[string]string{}}
    raw,err:=json.Marshal(s);if err!=nil{return err}
    annotations[SnapshotAnnotation]=string(raw);v.SetAnnotations(annotations)
    return b.Patch(ctx,v,client.MergeFromWithOptions(old,client.MergeFromWithOptimisticLock{}))
}
func (b *Base) recordLegacyVMI(ctx context.Context,v *unstructured.Unstructured)(bool,error){
    if v.GetAnnotations()[LegacyVMIAnnotation]==string(v.GetUID()){return false,nil}
    old:=v.DeepCopy();annotations:=v.GetAnnotations();if annotations==nil{annotations=map[string]string{}}
    annotations[LegacyVMIAnnotation]=string(v.GetUID());v.SetAnnotations(annotations)
    return true,b.Patch(ctx,v,client.MergeFromWithOptions(old,client.MergeFromWithOptimisticLock{}))
}
// Current request values deliberately do not replace a previously admitted
// snapshot. Only immutable identity, approval ceiling and VM incarnation matter.
func (b *Base) snapshotRequest(ctx context.Context,v *unstructured.Unstructured,s *requestSnapshot)(*api.FlytGPURequest,error){
    r:=&api.FlytGPURequest{}
    if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:v.GetNamespace(),Name:s.Allocation.RequestRef.Name},r);err!=nil{return nil,err}
    if string(r.UID)!=s.Allocation.RequestRef.UID||!r.DeletionTimestamp.IsZero(){return nil,fmt.Errorf("request UID is stale or request is terminating")}
    if r.Spec.VMRef!=s.Allocation.VMRef||r.Spec.ProfileRef!=s.ProfileRef||r.Spec.ControlPlaneRef!=s.ControlPlaneRef||r.Generation<s.Allocation.Generation{return nil,fmt.Errorf("request snapshot references differ")}
    if !matchesVM(v,r.Spec.VMRef){return nil,fmt.Errorf("VMI is not controlled by the requested VM UID")}
    if _,err:=b.requestVM(ctx,r);err!=nil{return nil,err}
    return r,nil
}
func (b *Base) workerRequest(ctx context.Context,w *api.FlytWorker,v *unstructured.Unstructured,p *api.FlytGPUProfile)error{
    if w.Spec.Request==nil{return nil}
    s,err:=readSnapshot(v);if err!=nil{return err}
    if s==nil||s.Allocation!=*w.Spec.Request||s.ProfileRef!=w.Spec.ProfileRef||s.ControlPlaneRef!=w.Spec.ControlPlaneRef{return fmt.Errorf("Worker allocation does not match the frozen VMI snapshot")}
    if _,err=b.snapshotRequest(ctx,v,s);err!=nil{return err}
    return withinProfile(s.Allocation.Resources,p)
}

// Both HAMi limits and the runtime byte guard consume this same normalized value.
func allocation(w *api.FlytWorker,p *api.FlytGPUProfile)api.GPUResources{
    if w.Spec.Request!=nil{return w.Spec.Request.Resources}
    return api.GPUResources{Count:1,Compute:p.Spec.Cores,MemoryMiB:p.Spec.MemoryMiB}
}
